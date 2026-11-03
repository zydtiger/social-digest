import datetime as dt
import subprocess
from pathlib import Path

import pytest

from social_digest.config import Config, load_config
from social_digest.note import create_note, note_name, render_note, write_note
from social_digest.pipeline import execute
from social_digest.store import fingerprint, read, write


@pytest.fixture
def config(tmp_path):
    return Config.model_validate(
        {
            "paths": {
                "run_dir": tmp_path / "runs",
                "note_dir": tmp_path / "vault/Calendar/Social Digests",
                "parent_note": tmp_path / "vault/Atlas/Calendar.md",
            },
            "model": {"base_url": "http://localhost:1234/v1", "model": "test-model"},
            "topic": "useful software",
            "agent": {"command": ["test-agent"]},
        }
    )


@pytest.fixture
def cards():
    return [
        {
            "id": "twitter:1",
            "title": "Useful | software",
            "url": "https://x.com/i/status/1",
            "author": "source_author",
            "published_at": "2026-11-02T01:00:00+00:00",
            "score": 8,
            "claims": [{"claim": "A release", "excerpt": "Released today"}],
        }
    ]


def parent(config):
    path = config.paths.parent_note
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("## Calendar\n\n- [[Existing note]]\n")
    return path


def seed_run(config, cards, monkeypatch, legacy=False):
    directory = config.paths.run_dir / "2026-10-27_2026-11-04"
    directory.mkdir(parents=True)
    for stage in ("collect", "score", "enrich", "evidence"):
        write(directory / f"{stage}.json", cards)
    write(directory / "cards.json", {"cards": cards, "coverage": [], "events": []})
    write(
        directory / "requests.json",
        {"saved-request": {"provider": "twitter", "reserved_micro": 15000, "status": "complete"}},
    )
    if legacy:
        settings = config.model_dump(mode="json")
        settings["run_dir"] = settings.pop("paths")["run_dir"]
        settings.pop("budget")
        settings["agent"] = {"language": config.agent.language}
        identity = {"config": settings, "since": "2026-10-27", "until": "2026-11-04", "inputs": []}
        write(directory / "run.json", {"signature": fingerprint(identity), **identity})
    calls = []

    def agent(*args, **kwargs):
        calls.append(kwargs["input"])
        return subprocess.CompletedProcess(args, 0, "- A concrete release.\n", "")

    monkeypatch.setattr("social_digest.agent.subprocess.run", agent)
    monkeypatch.setattr("requests.Session.get", lambda *_a, **_kw: pytest.fail("provider recalled"))
    monkeypatch.setattr("requests.post", lambda *_a, **_kw: pytest.fail("local model recalled"))
    return directory, calls


def test_weekly_note_frontmatter_sections_and_evidence(config, cards):
    text = render_note(
        dt.date(2026, 11, 4), "- A concrete release.", cards, config.paths.parent_note
    )
    assert note_name(dt.date(2026, 11, 4)) == "2026-W45 Social Digest"
    assert note_name(dt.date(2027, 1, 4)) == "2026-W53 Social Digest"
    assert 'up: "[[Calendar]]"' in text
    assert "  - review/social-digest" in text
    assert 'reported: ["twitter:1"]' in text
    assert text.split("---", 2)[2].lstrip().startswith("## Developments")
    assert "Useful \\| software" in text
    assert "source_author" in text
    assert "2026-11-02T01:00:00+00:00" in text
    assert "https://x.com/i/status/1" in text
    assert "## Sources and evidence" in text
    heading = render_note(
        dt.date(2026, 11, 4), "# Title\n\n## Details\nText", cards, config.paths.parent_note
    )
    assert "\n# " not in heading


def test_write_and_completed_resume_preserve_note_edits(config, cards, monkeypatch):
    calendar = parent(config)
    directory, calls = seed_run(config, cards, monkeypatch)
    first = execute(config, "2026-10-27", "2026-11-04", {})
    note = Path(first["note"])
    assert first["stage"] == "write"
    assert first["digest"] == str(directory / "digest.md")
    assert note.name == "2026-W45 Social Digest.md"
    assert note.is_file()
    assert calendar.read_text().count("[[2026-W45 Social Digest]]") == 1
    note.write_text(note.read_text() + "\nMy own observation.\n")
    before = {
        p: p.read_bytes()
        for p in (
            note,
            calendar,
            directory / "digest.md",
            directory / "agent.json",
            directory / "requests.json",
        )
    }
    second = execute(config, "2026-10-27", "2026-11-04", {})
    assert second["note"] == first["note"]
    assert len(calls) == 1
    assert before == {p: p.read_bytes() for p in before}


def test_note_collision_and_parent_link_alias_dedup(config, cards, tmp_path):
    calendar = parent(config)
    config.paths.note_dir.mkdir(parents=True)
    unrelated = config.paths.note_dir / "2026-W45 Social Digest.md"
    unrelated.write_text("Unrelated hand-written note\n")
    calendar.write_text(calendar.read_text() + "- [[2026-W45 Social Digest 2|Weekly social]]\n")
    directory = tmp_path / "run"
    directory.mkdir()
    note = write_note(directory, config.paths, dt.date(2026, 11, 4), "## News\nText", cards)
    assert note.name == "2026-W45 Social Digest 2.md"
    assert unrelated.read_text() == "Unrelated hand-written note\n"
    assert "[[2026-W45 Social Digest 2]]" not in calendar.read_text()
    assert calendar.read_text().count("Weekly social") == 1


def test_missing_parent_retries_write_without_paid_recall(config, cards, monkeypatch):
    directory, calls = seed_run(config, cards, monkeypatch)
    with pytest.raises(FileNotFoundError, match="parent note not found"):
        execute(config, "2026-10-27", "2026-11-04", {})
    assert len(calls) == 1
    assert (directory / "digest.md").exists()
    parent(config)
    result = execute(config, "2026-10-27", "2026-11-04", {}, stage="write")
    assert Path(result["note"]).is_file()
    assert len(calls) == 1


def test_output_paths_do_not_invalidate_paid_identity(config, cards, monkeypatch, tmp_path):
    directory, calls = seed_run(config, cards, monkeypatch, legacy=True)
    before = (directory / "run.json").read_bytes()
    result = execute(config, "2026-10-27", "2026-11-04", {}, stage="synthesize")
    assert result["note"] is None
    assert not config.paths.note_dir.exists()
    config.paths.note_dir = tmp_path / "other-vault/Calendar/Social Digests"
    config.paths.parent_note = tmp_path / "other-vault/Atlas/Calendar.md"
    parent(config)
    result = execute(config, "2026-10-27", "2026-11-04", {}, stage="write")
    assert Path(result["note"]).parent == config.paths.note_dir
    assert len(calls) == 1
    assert before == (directory / "run.json").read_bytes()
    config.paths.note_dir = tmp_path / "another-destination"
    with pytest.raises(ValueError, match="output paths differ"):
        execute(config, "2026-10-27", "2026-11-04", {}, stage="write")
    assert not config.paths.note_dir.exists()
    config.topic = "different computation"
    with pytest.raises(ValueError, match="configuration differs"):
        execute(config, "2026-10-27", "2026-11-04", {})


def test_dry_run_does_not_write_digest_or_vault(config, cards, monkeypatch):
    directory, calls = seed_run(config, cards, monkeypatch)
    result = execute(config, "2026-10-27", "2026-11-04", {}, dry_run=True, stage="write")
    assert result["stage"] == "cards"
    assert result["note"] is None
    assert not (directory / "digest.md").exists()
    assert not config.paths.note_dir.exists()
    assert not config.paths.parent_note.exists()
    assert not calls


def test_configured_paths_expand_without_personal_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / "config.toml"
    config_file.write_text("""topic = "software"
[paths]
run_dir = "~/state/runs"
note_dir = "~/vault/Calendar/Social Digests"
parent_note = "~/vault/Atlas/Calendar.md"
[model]
base_url = "http://localhost:1234/v1"
model = "test-model"
""")
    cfg = load_config(config_file)
    assert cfg.paths.run_dir == tmp_path / "state/runs"
    assert cfg.paths.note_dir == tmp_path / "vault/Calendar/Social Digests"
    assert cfg.paths.parent_note == tmp_path / "vault/Atlas/Calendar.md"
    config_file.write_text(config_file.read_text().replace('"~/state/runs"', '"relative/runs"'))
    with pytest.raises(ValueError, match="must be absolute"):
        load_config(config_file)


def test_exclusive_note_create_never_overwrites_foreign_content(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("Foreign content")
    with pytest.raises(FileExistsError):
        create_note(path, "Generated content")
    assert path.read_text() == "Foreign content"


def test_interrupted_parent_write_preserves_own_note_on_retry(config, cards, monkeypatch, tmp_path):
    from social_digest import note as module

    calendar = parent(config)
    directory = tmp_path / "run"
    directory.mkdir()
    actual_write = module.write_text
    monkeypatch.setattr(
        module, "write_text", lambda *_: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(OSError, match="disk full"):
        write_note(directory, config.paths, dt.date(2026, 11, 4), "## News\nText", cards)
    checkpoint = read(directory / "write.json")
    assert checkpoint["created"] is True
    output = Path(checkpoint["note"])
    output.write_text(output.read_text() + "\nManual review.\n")
    before = output.read_bytes()
    monkeypatch.setattr(module, "write_text", actual_write)
    retried = write_note(directory, config.paths, dt.date(2026, 11, 4), "## News\nText", cards)
    assert retried == output
    assert output.read_bytes() == before
    assert calendar.read_text().count(f"[[{output.stem}]]") == 1
    assert read(directory / "write.json")["status"] == "complete"
