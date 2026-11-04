"""Standalone CLI entry point."""

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from .config import config_dir, load_config, load_env
from .pipeline import STAGES, execute


def date_window(
    since: dt.date | None = None,
    until: dt.date | None = None,
    weekly: bool = False,
    *,
    now: dt.datetime | None = None,
) -> tuple[dt.date, dt.date]:
    if weekly and (since is not None or until is not None):
        raise ValueError("--weekly cannot be combined with --since or --until")
    moment = now or dt.datetime.now(dt.UTC)
    end = until or moment.astimezone(dt.UTC).date()
    start = since or end - dt.timedelta(days=7 if weekly else 1)
    if not start < end or (end - start).days > 366:
        raise ValueError("window must be 1..366 days; --until is exclusive (UTC)")
    return start, end


def note_result(note: Path) -> dict[str, str] | None:
    """Describe an existing note relative to its nearest Obsidian vault."""
    note = note.resolve()
    if not note.is_file():
        return None
    for parent in note.parents:
        if (parent / ".obsidian").is_dir():
            return {
                "vault": parent.name,
                "note": note.relative_to(parent).as_posix(),
                "summary": f"Open {note.stem}",
            }
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="social-digest")
    parser.add_argument("--config", type=Path, default=config_dir() / "config.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("config")
    check.add_argument("action", choices=["path", "check"])
    run = commands.add_parser("run")
    run.add_argument("--since", type=dt.date.fromisoformat)
    run.add_argument("--until", type=dt.date.fromisoformat)
    run.add_argument(
        "--weekly", action="store_true", help="cover the previous seven complete UTC days"
    )
    run.add_argument("--stage", choices=STAGES)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--input-run", type=Path, action="append", default=[])
    run.add_argument("--retry-agent", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "run" and args.weekly and (args.since or args.until):
        parser.error("--weekly cannot be combined with --since or --until")
    try:
        if args.command == "config" and args.action == "path":
            print(args.config)
            return 0
        config = load_config(args.config)
        env = dict(os.environ)
        load_env(args.config.parent / "env", env)
        if args.command == "config":
            print("Configuration valid")
            return 0
        since, until = date_window(args.since, args.until, args.weekly)
        result = execute(
            config,
            since.isoformat(),
            until.isoformat(),
            env,
            stage=args.stage,
            dry_run=args.dry_run,
            input_runs=tuple(args.input_run),
            retry_agent=args.retry_agent,
        )
        result["notification"] = note_result(Path(result["note"])) if result.get("note") else None
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        # Validation errors can include user config values. Never echo provider response
        # bodies, subprocess stderr, keys, or Pydantic input_value representations.
        from pydantic import ValidationError

        if isinstance(error, ValidationError):
            message = "; ".join(
                ".".join(map(str, e["loc"])) + ": " + e["msg"]
                for e in error.errors(include_input=False)
            )
        elif isinstance(error, (ValueError, RuntimeError, FileNotFoundError)):
            message = str(error)
        else:
            message = type(error).__name__
        print(f"social-digest: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
