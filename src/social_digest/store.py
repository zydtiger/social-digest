"""Atomic checkpoints and a conservative, durable paid-request ledger."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def read(path: Path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Distinct model tasks may have identical content and finish concurrently.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def jsonl(path: Path, items: list[dict]) -> None:
    write_text(path, "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items))


class BudgetExceeded(RuntimeError):
    pass


class UncertainRequest(RuntimeError):
    pass


class Ledger:
    def __init__(self, directory: Path, max_requests: int, max_cost_usd: float):
        self.directory = directory
        self.path = directory / "requests.json"
        self.entries = read(self.path, {})
        self.max_requests = max_requests
        self.max_micro = round(max_cost_usd * 1_000_000)

    def summary(self) -> dict:
        return {
            "requests": len(self.entries),
            "reserved_cost_usd": sum(e["reserved_micro"] for e in self.entries.values())
            / 1_000_000,
        }

    def cached(self, key: str):
        entry = self.entries.get(key)
        if not entry:
            return None
        path = self.directory / "raw" / f"{key}.json"
        if path.exists():
            return read(path)
        raise UncertainRequest(
            f"request {key[:12]} has no durable response; refusing automatic re-billing"
        )

    def reserve(self, key: str, provider: str, micro: int) -> None:
        spent = sum(e["reserved_micro"] for e in self.entries.values())
        if len(self.entries) >= self.max_requests or spent + micro > self.max_micro:
            raise BudgetExceeded("provider request/cost budget exhausted")
        self.entries[key] = {"provider": provider, "reserved_micro": micro, "status": "pending"}
        write(self.path, self.entries)

    def complete(self, key: str, response: dict) -> None:
        write(self.directory / "raw" / f"{key}.json", response)
        self.entries[key]["status"] = "complete"
        write(self.path, self.entries)
