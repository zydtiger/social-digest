"""Resumable bounded pipeline, one writer per run directory."""

import fcntl
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from .agent import prompt, synthesize
from .config import Config
from .model import LocalModel
from .note import write_note
from .providers import Providers, collect, enrich
from .store import BudgetExceeded, Ledger, fingerprint, jsonl, read, write

STAGES = ("collect", "score", "enrich", "evidence", "cards", "synthesize", "write")


def computation_settings(settings: dict) -> dict:
    """Normalize current and saved settings to the paid computation's identity."""
    settings = dict(settings)
    settings.pop("paths", None)
    settings.pop("run_dir", None)
    settings.pop("budget", None)
    settings["agent"] = {"language": settings["agent"]["language"]}
    return settings


def execute(
    config: Config,
    since: str,
    until: str,
    env: dict[str, str],
    stage: str | None = None,
    dry_run: bool = False,
    input_runs: tuple[Path, ...] = (),
    retry_agent: bool = False,
) -> dict:
    directory = config.paths.run_dir / f"{since}_{until}"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another process owns this run") from None
        return _execute(
            config, directory, since, until, env, stage, dry_run, input_runs, retry_agent
        )


def _execute(config, directory, since, until, env, stage, dry_run, input_runs, retry_agent):
    settings = computation_settings(config.model_dump(mode="json"))
    imported = []
    for source in input_runs:
        path = source / "collect.json"
        if source.resolve() == directory.resolve() or not path.exists():
            raise ValueError("input-run must reference another completed collection")
        imported.append((source.resolve(), read(path)))
    identity = {
        "config": settings,
        "since": since,
        "until": until,
        "inputs": [
            {"path": str(path), "collect_sha256": fingerprint(items)} for path, items in imported
        ],
    }
    signature = fingerprint(identity)
    existing = read(directory / "run.json")
    if existing:
        saved = {key: existing[key] for key in ("config", "since", "until", "inputs")}
        saved["config"] = computation_settings(saved["config"])
        if fingerprint(saved) != signature:
            raise ValueError(
                "run configuration differs; use a different run_dir or original config"
            )
    if not existing:
        write(directory / "run.json", {"signature": signature, **identity})
    ledger = Ledger(directory, config.budget.max_requests, config.budget.max_cost_usd)
    api = Providers(config, ledger, env)
    model = LocalModel(config, directory)
    stop = "cards" if dry_run else (stage or "write")
    if dry_run and stage and STAGES.index(stage) < STAGES.index("cards"):
        stop = stage
    coverage = read(directory / "coverage.json", [])

    def cached(name, function):
        path = directory / f"{name}.json"
        if path.exists():
            value = read(path)
            if isinstance(value, list) and not (directory / f"{name}.jsonl").exists():
                jsonl(directory / f"{name}.jsonl", value)
            return value
        value = function()
        write(path, value)
        if isinstance(value, list):
            jsonl(directory / f"{name}.jsonl", value)
        return value

    def collection():
        if not input_runs:
            values = collect(api, since, until)
            return [{**x, "origin_run": str(directory.resolve())} for x in values]
        values = {}
        for _, source_items in imported:
            for item in source_items:
                date = (item.get("published_at") or "")[:10]
                if since <= date < until:
                    values.setdefault(item["id"], item)
        return list(values.values())

    items = cached("collect", collection)
    if stop == "collect":
        return summary(directory, stop, ledger, items)
    with ThreadPoolExecutor(max_workers=config.model.concurrency) as pool:
        scored = cached("score", lambda: list(pool.map(model.score, items)))
    selected = sorted(
        (x for x in scored if x["score"] >= config.min_score), key=lambda x: (-x["score"], x["id"])
    )[: config.max_cards]
    if stop == "score":
        return summary(directory, stop, ledger, selected)

    def enrichment():
        values = []
        for index, item in enumerate(selected):
            if index >= config.max_enrich:
                values.append(item)
                continue
            try:
                values.append(enrich(api, item, config.comments))
            except BudgetExceeded:
                message = "Enrichment stopped at provider budget; search text retained."
                if message not in coverage:
                    coverage.append(message)
                values.append(item)
        write(directory / "coverage.json", coverage)
        return values

    enriched = cached("enrich", enrichment)
    if stop == "enrich":
        return summary(directory, stop, ledger, enriched)
    with ThreadPoolExecutor(max_workers=config.model.concurrency) as pool:
        evidence = cached("evidence", lambda: list(pool.map(model.evidence, enriched)))
    if stop == "evidence":
        return summary(directory, stop, ledger, evidence)

    def cards():
        bounded = []
        payload = {"window": [since, until], "coverage": coverage, "events": [], "cards": []}
        for card in evidence:
            if not card["claims"]:
                continue
            card = {
                key: card[key]
                for key in ("id", "title", "score", "url", "author", "published_at", "claims")
            }
            trial = {**payload, "cards": [*bounded, card]}
            # Reserve room for event labels and coverage. UTF-8 bytes bound token count
            # conservatively without relying on the paid agent's tokenizer.
            if len(prompt(config, trial).encode()) > config.max_payload_tokens - 1000:
                continue
            bounded.append(card)
        payload["cards"] = bounded
        payload["events"] = model.cluster(bounded)
        while True:
            omitted = len(evidence) - len(payload["cards"])
            payload["coverage"] = [
                *coverage,
                f"{len(items)} collected; {len(selected)} selected; "
                f"{omitted} cards omitted for missing evidence or payload budget.",
            ]
            size = len(prompt(config, payload).encode())
            if size <= config.max_payload_tokens:
                break
            if not payload["cards"]:
                raise ValueError("prompt instructions exceed final prompt budget")
            removed = payload["cards"].pop()["id"]
            payload["events"] = [
                {
                    **group,
                    "ids": [identifier for identifier in group["ids"] if identifier != removed],
                }
                for group in payload["events"]
                if any(x != removed for x in group["ids"])
            ]
        write(
            directory / "payload-budget.json",
            {
                "method": "utf8_byte_upper_bound",
                "prompt_utf8_bytes": size,
                "estimated_token_upper_bound": size,
                "max_payload_tokens": config.max_payload_tokens,
            },
        )
        return payload

    payload = cached("cards", cards)
    if stop in ("synthesize", "write"):
        if not payload["cards"]:
            raise ValueError("no verified evidence cards; refusing empty paid synthesis")
        digest = synthesize(config, directory, payload, retry_agent)
        if stop == "write":
            write_note(
                directory,
                config.paths,
                date.fromisoformat(until),
                digest.read_text(),
                payload["cards"],
            )
    return summary(directory, stop, ledger, payload["cards"])


def summary(directory, stage, ledger, items):
    return {
        "stage": stage,
        "run_dir": str(directory),
        "items": len(items),
        **ledger.summary(),
        "digest": str(directory / "digest.md") if (directory / "digest.md").exists() else None,
        "payload_budget": read(directory / "payload-budget.json"),
        "note": read(directory / "write.json", {}).get("note"),
    }
