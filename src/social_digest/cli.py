"""Standalone CLI entry point."""

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from .config import config_dir, load_config, load_env
from .pipeline import STAGES, execute


def main() -> int:
    parser = argparse.ArgumentParser(prog="social-digest")
    parser.add_argument("--config", type=Path, default=config_dir() / "config.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("config")
    check.add_argument("action", choices=["path", "check"])
    run = commands.add_parser("run")
    run.add_argument("--since", type=dt.date.fromisoformat)
    run.add_argument("--until", type=dt.date.fromisoformat)
    run.add_argument("--stage", choices=STAGES)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--input-run", type=Path, action="append", default=[])
    run.add_argument("--retry-agent", action="store_true")
    args = parser.parse_args()
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
        until = args.until or dt.datetime.now(dt.UTC).date()
        since = args.since or until - dt.timedelta(days=1)
        if not since < until or (until - since).days > 366:
            raise ValueError("window must be 1..366 days; --until is exclusive (UTC)")
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
