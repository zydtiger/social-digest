import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest

from social_digest import agent
from social_digest.config import Config, load_env
from social_digest.model import LocalModel, parse_json
from social_digest.pipeline import execute
from social_digest.providers import Providers, collect, enrich, successful, timestamp
from social_digest.store import BudgetExceeded, Ledger, UncertainRequest, read


@pytest.fixture
def config(tmp_path):
    return Config.model_validate(
        {
            "run_dir": str(tmp_path / "runs"),
            "topic": "useful software",
            "model": {"base_url": "http://localhost:1234/v1", "model": "test-model"},
            "twitter": {"enabled": True, "queries": ["software"], "max_pages": 2},
            "xhs": {"enabled": False},
            "max_enrich": 0,
        }
    )


def tweet(identifier="1", text="Useful new software release"):
    return {"id": identifier, "text": text, "createdAt": "2026-11-02T01:00:00Z"}


class Response:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


def test_ledger_crash_boundaries(tmp_path):
    ledger = Ledger(tmp_path, 2, 0.02)
    ledger.reserve("one", "xhs", 10000)
    with pytest.raises(UncertainRequest):
        Ledger(tmp_path, 2, 0.02).cached("one")
    ledger.complete("one", {"ok": True})
    resumed = Ledger(tmp_path, 2, 0.02)
    assert resumed.cached("one") == {"ok": True}
    resumed.reserve("two", "xhs", 10000)
    with pytest.raises(BudgetExceeded):
        resumed.reserve("three", "xhs", 1)
    assert resumed.summary() == {"requests": 2, "reserved_cost_usd": 0.02}


def test_cost_cap_precedes_dispatch(config, tmp_path):
    config.budget.max_cost_usd = 0.001
    api = Providers(config, Ledger(tmp_path, 10, 0.001), {"TWITTERAPI_API_KEY": "secret"})
    api.session.get = lambda *a, **kw: pytest.fail("request dispatched above budget")
    with pytest.raises(BudgetExceeded):
        api.get("twitter", "/twitter/tweet/advanced_search", {"query": "test"})


def test_provider_cached_on_success_and_failure(config, tmp_path):
    api = Providers(config, Ledger(tmp_path, 10, 0.20), {"TIKHUB_API_KEY": "secret"})
    calls = []

    def fetch(*a, **kw):
        calls.append(kw)
        return Response({"code": 200, "data": {"success": False, "code": -1, "data": {}}})

    api.session.get = fetch
    for _ in range(2):
        with pytest.raises(RuntimeError, match="upstream"):
            api.get("xhs", "/search_notes", {"keyword": "test"})
    assert len(calls) == 1
    assert calls[0]["allow_redirects"] is False
    assert api.ledger.summary()["reserved_cost_usd"] == 0.01
    assert "secret" not in (tmp_path / "requests.json").read_text()


def test_collect_pagination_dedup_dates(config):
    class API:
        def __init__(self):
            self.config, self.calls = config, []

        def get(self, provider, endpoint, params):
            self.calls.append(params)
            outside = {**tweet("old"), "createdAt": "2026-10-30T00:00:00Z"}
            return {
                "tweets": [tweet(), outside],
                "has_next_page": True,
                "next_cursor": "next",
            }, "raw/test.json"

    api = API()
    values = collect(api, "2026-11-02", "2026-11-03")
    assert [x["id"] for x in values] == ["twitter:1"]
    assert len(api.calls) == 2
    assert api.calls[1]["cursor"] == "next"
    assert "since:2026-11-02 until:2026-11-03" in api.calls[0]["query"]


def test_xhs_pagination_session_and_shape(config):
    config.twitter.enabled = False
    config.xhs.enabled, config.xhs.queries, config.xhs.max_pages = True, ["工具"], 2
    calls = []

    class API:
        def __init__(self):
            self.config = config

        def get(self, provider, endpoint, params):
            calls.append(params)
            return {
                "data": {
                    "data": {
                        "items": [
                            {
                                "model_type": "note",
                                "note": {
                                    "id": "abc",
                                    "title": "Tool",
                                    "desc": "Useful",
                                    "type": "normal",
                                    "timestamp": 1793581200,
                                },
                            }
                        ]
                    },
                    "search_id": "s1",
                    "search_session_id": "s2",
                    "next_page": 2,
                }
            }, "raw/example.json"

    result = collect(API(), "2026-11-01", "2026-11-03")
    assert len(result) == 1
    assert calls[1]["search_id"] == "s1"
    assert calls[1]["search_session_id"] == "s2"


def test_xhs_nested_detail(config):
    class API:
        def get(self, provider, endpoint, params):
            assert endpoint == "/get_image_note_detail"
            return {
                "data": {
                    "data": [
                        {"note_list": [{"id": "abc", "title": "Full", "desc": "complete text"}]}
                    ]
                }
            }, "detail.json"

    value = enrich(
        API(),
        {
            "provider": "xhs",
            "source_id": "abc",
            "kind": "normal",
            "text": "Short",
            "raw": ["search.json"],
        },
        False,
    )
    assert value["text"] == "Full\ncomplete text"


def test_env_permissions_precedence_and_no_shell(tmp_path):
    path = tmp_path / "env"
    path.write_text("TIKHUB_API_KEY=$(never-run)\nTWITTERAPI_API_KEY=from-file\n")
    path.chmod(0o600)
    env = {"TWITTERAPI_API_KEY": "existing"}
    load_env(path, env)
    assert env == {"TWITTERAPI_API_KEY": "existing", "TIKHUB_API_KEY": "$(never-run)"}
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        load_env(path, {})


def test_parse_and_verified_evidence(config, tmp_path, monkeypatch):
    assert parse_json('<think>reasoning</think>```json\n{"score":8}\n```') == {"score": 8}
    model = LocalModel(config, tmp_path)
    monkeypatch.setattr(
        model,
        "ask",
        lambda *_: {
            "title": "Release",
            "claims": [
                {"claim": "supported", "source": "post", "excerpt": "new software"},
                {"claim": "invented", "source": "post", "excerpt": "not in source"},
                {"claim": "empty", "source": "post", "excerpt": ""},
            ],
        },
    )
    item = {
        "id": "twitter:1",
        "text": "Useful new software release",
        "score": 8,
        "url": "https://x.com/i/status/1",
        "raw": [],
        "origin_run": str(tmp_path),
        "author": "example_author",
        "published_at": "2026-11-02T01:00:00+00:00",
    }
    evidence = model.evidence(item)
    assert evidence["author"] == "example_author"
    assert evidence["published_at"] == item["published_at"]
    assert len(evidence["claims"]) == 1
    claim = evidence["claims"][0]
    assert item["text"][claim["start"] : claim["end"]] == claim["excerpt"]


def test_cluster_rejects_unknown_and_duplicate_ids(config, tmp_path, monkeypatch):
    model = LocalModel(config, tmp_path)
    monkeypatch.setattr(model, "ask", lambda *_: {"groups": [{"event": "x", "ids": ["1", "1"]}]})
    with pytest.raises(ValueError, match="partition"):
        model.cluster([{"id": "1", "title": "a"}, {"id": "2", "title": "b"}])


def test_model_cache(config, tmp_path, monkeypatch):
    from social_digest import model as module

    calls = []

    def fetch(*args, **kwargs):
        calls.append(kwargs)
        return Response({"choices": [{"message": {"content": '{"score":8,"reason":"yes"}'}}]})

    monkeypatch.setattr(module.requests, "post", fetch)
    model = LocalModel(config, tmp_path)
    item = {"text": "Useful"}
    assert model.score(item)["score"] == 8
    assert model.score(item)["score"] == 8
    assert len(calls) == 1


def mock_pipeline(monkeypatch):
    from social_digest import pipeline

    calls = []

    def collect_once(*_):
        calls.append("collect")
        return [
            {
                "id": "twitter:1",
                "provider": "twitter",
                "source_id": "1",
                "url": "https://x.com/i/status/1",
                "published_at": "2026-11-02T01:00:00Z",
                "author": "example_author",
                "text": "new software",
                "kind": "tweet",
                "raw": [],
            }
        ]

    monkeypatch.setattr(pipeline, "collect", collect_once)
    monkeypatch.setattr(LocalModel, "score", lambda self, x: {**x, "score": 8, "reason": "x"})
    monkeypatch.setattr(
        LocalModel,
        "evidence",
        lambda self, x: {
            "id": x["id"],
            "url": x["url"],
            "author": x["author"],
            "published_at": x["published_at"],
            "title": "software",
            "score": 8,
            "claims": [{"claim": "released", "excerpt": "software", "source": "post"}],
        },
    )
    return calls


def test_pipeline_dry_run_resume_and_identity(config, monkeypatch):
    calls = mock_pipeline(monkeypatch)
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **kw: pytest.fail("paid agent called"))
    a = execute(config, "2026-11-02", "2026-11-03", {}, dry_run=True)
    b = execute(config, "2026-11-02", "2026-11-03", {}, dry_run=True)
    assert a == b
    assert calls == ["collect"]
    assert a["stage"] == "cards"
    config.topic = "another topic"
    with pytest.raises(ValueError, match="configuration differs"):
        execute(config, "2026-11-02", "2026-11-03", {})


def test_daily_imports_dedup_without_fetch(config, monkeypatch):
    calls = mock_pipeline(monkeypatch)
    first = execute(config, "2026-11-02", "2026-11-03", {}, stage="collect")
    weekly = execute(
        config,
        "2026-10-27",
        "2026-11-03",
        {},
        stage="collect",
        input_runs=(Path(first["run_dir"]), Path(first["run_dir"])),
    )
    assert weekly["items"] == 1
    assert calls == ["collect"]


def test_agent_secrets_and_no_automatic_retry(config, tmp_path, monkeypatch):
    config.agent.command = [sys.executable, "-c", "unused"]
    monkeypatch.setenv("TWITTERAPI_API_KEY", "provider-secret")
    monkeypatch.setenv("TIKHUB_API_KEY", "other-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-secret")
    calls = []

    def fail(*args, **kwargs):
        calls.append(kwargs)
        assert not any("secret" in x for x in kwargs["env"].values())
        return subprocess.CompletedProcess(args, 1, "", "sensitive error")

    monkeypatch.setattr(agent.subprocess, "run", fail)
    with pytest.raises(ValueError, match="no usable"):
        agent.synthesize(config, tmp_path, {"cards": []})
    with pytest.raises(ValueError, match="retry-agent"):
        agent.synthesize(config, tmp_path, {"cards": []})
    assert len(calls) == 1
    monkeypatch.setattr(
        agent.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, "## Digest\nEvidence", ""),
    )
    path = agent.synthesize(config, tmp_path, {"cards": []}, retry=True)
    assert path.exists()
    assert read(tmp_path / "agent.json")["attempts"] == 2
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **kw: pytest.fail("duplicate billing"))
    assert agent.synthesize(config, tmp_path, {"cards": []}) == path


def test_agent_prompt_budget(config, tmp_path):
    config.agent.command = ["nonexistent"]
    with pytest.raises(ValueError, match="token budget"):
        agent.synthesize(config, tmp_path, {"cards": ["x" * 10000]})
    assert not (tmp_path / "agent.json").exists()


@pytest.mark.parametrize(
    "value", ["2026-11-02T01:00:00Z", "Mon Nov 02 01:00:00 +0000 2026", 1793581200, 1793581200000]
)
def test_timestamp(value):
    assert dt.datetime.fromisoformat(timestamp(value)).tzinfo == dt.UTC


def test_upstream_http_200_error():
    with pytest.raises(RuntimeError):
        successful("xhs", {"code": 200, "data": {"success": False, "code": 300011, "data": {}}})


def test_lock(config, monkeypatch):
    import fcntl

    directory = config.run_dir / "2026-11-02_2026-11-03"
    directory.mkdir(parents=True)
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="another process"):
            execute(config, "2026-11-02", "2026-11-03", {})


def test_partial_collection_resume_with_raised_cap(config, monkeypatch):
    from social_digest.providers import TWITTER

    calls = []
    config.budget.max_requests = 1

    def get(_self, url, **kwargs):
        assert url == TWITTER + "/twitter/tweet/advanced_search"
        cursor = kwargs["params"]["cursor"]
        calls.append(cursor)
        return Response(
            {
                "tweets": [tweet("2" if cursor else "1")],
                "has_next_page": not cursor,
                "next_cursor": "page2",
            }
        )

    import requests

    monkeypatch.setattr(requests.Session, "get", get)
    with pytest.raises(BudgetExceeded):
        execute(
            config, "2026-11-02", "2026-11-03", {"TWITTERAPI_API_KEY": "secret"}, stage="collect"
        )
    config.budget.max_requests = 2
    result = execute(
        config, "2026-11-02", "2026-11-03", {"TWITTERAPI_API_KEY": "secret"}, stage="collect"
    )
    assert calls == ["", "page2"]
    assert result["items"] == 2
    assert result["requests"] == 2


def test_final_payload_byte_accounting(config, monkeypatch):
    mock_pipeline(monkeypatch)
    config.max_payload_tokens = 1500
    result = execute(config, "2026-11-02", "2026-11-03", {}, dry_run=True)
    assert result["payload_budget"]["method"] == "utf8_byte_upper_bound"
    assert result["payload_budget"]["prompt_utf8_bytes"] <= 1500


def test_atomic_identical_writes_are_thread_safe(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from social_digest.store import write

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: write(tmp_path / "same.json", {"complete": True}), range(20)))
    assert read(tmp_path / "same.json") == {"complete": True}


def test_http_error_is_cached_as_failure(config, tmp_path):
    api = Providers(config, Ledger(tmp_path, 10, 0.2), {"TWITTERAPI_API_KEY": "secret"})
    response = Response({"message": "denied"})
    response.status_code = 403
    api.session.get = lambda *a, **kw: response
    with pytest.raises(RuntimeError, match="HTTP 403"):
        api.get("twitter", "/twitter/tweets", {"tweet_ids": "1"})
    api.session.get = lambda *a, **kw: pytest.fail("cached denial retried")
    with pytest.raises(RuntimeError, match="unusable"):
        api.get("twitter", "/twitter/tweets", {"tweet_ids": "1"})


def test_resume_repairs_missing_jsonl_without_fetch(config, monkeypatch):
    mock_pipeline(monkeypatch)
    first = execute(config, "2026-11-02", "2026-11-03", {}, stage="collect")
    directory = Path(first["run_dir"])
    (directory / "collect.jsonl").unlink()
    from social_digest import pipeline

    monkeypatch.setattr(pipeline, "collect", lambda *_: pytest.fail("collection repeated"))
    execute(config, "2026-11-02", "2026-11-03", {}, stage="collect")
    assert (directory / "collect.jsonl").exists()


def test_no_paid_synthesis_for_empty_evidence(config, monkeypatch):
    mock_pipeline(monkeypatch)
    config.min_score = 10
    from social_digest import pipeline

    monkeypatch.setattr(pipeline, "synthesize", lambda *_: pytest.fail("empty paid synthesis"))
    with pytest.raises(ValueError, match="no verified evidence"):
        execute(config, "2026-11-02", "2026-11-03", {})


def test_cards_preserve_author_and_date(config, monkeypatch):
    mock_pipeline(monkeypatch)
    result = execute(config, "2026-11-02", "2026-11-03", {}, dry_run=True)
    payload = read(Path(result["run_dir"]) / "cards.json")
    assert payload["cards"][0]["author"] == "example_author"
    assert payload["cards"][0]["published_at"] == "2026-11-02T01:00:00Z"


def test_xhs_source_url_preserves_encoded_token():
    from urllib.parse import parse_qs, urlsplit

    from social_digest.providers import xhs_item

    token = "signed+/= token&"
    item = xhs_item({"id": "example-note", "xsec_token": token}, "raw/example.json")
    parsed = urlsplit(item["url"])
    assert parsed.path == "/explore/example-note"
    assert parse_qs(parsed.query) == {"xsec_token": [token]}
    assert " " not in item["url"]
    assert xhs_item({"id": "example-note"}, "raw/example.json")["url"] == (
        "https://www.xiaohongshu.com/explore/example-note"
    )


def test_import_content_change_rejects_stale_cache(config, monkeypatch):
    from social_digest.store import write

    mock_pipeline(monkeypatch)
    daily = execute(config, "2026-11-02", "2026-11-03", {}, stage="collect")
    source = Path(daily["run_dir"])
    kwargs = {"stage": "collect", "input_runs": (source,)}
    execute(config, "2026-10-27", "2026-11-03", {}, **kwargs)
    collection = read(source / "collect.json")
    collection[0]["text"] = "Changed source collection"
    write(source / "collect.json", collection)
    with pytest.raises(ValueError, match="configuration differs"):
        execute(config, "2026-10-27", "2026-11-03", {}, **kwargs)
