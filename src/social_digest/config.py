"""User settings and private, process-local environment loading."""

import os
import re
import stat
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Model(Settings):
    base_url: str
    model: str
    concurrency: int = Field(default=4, ge=1, le=16)
    max_input_bytes: int = Field(default=24000, ge=1000, le=100000)
    max_output_tokens: int = Field(default=1200, ge=128, le=8000)
    timeout_seconds: int = Field(default=180, ge=1, le=600)


class Source(Settings):
    enabled: bool = True
    queries: list[str] = Field(default_factory=list, max_length=20)
    watchlist: list[str] = Field(default_factory=list, max_length=50)
    max_pages: int = Field(default=1, ge=1, le=10)
    sort: str = "Latest"


class XHSSource(Source):
    sort: str = "time_descending"


class Budget(Settings):
    max_requests: int = Field(default=20, ge=0, le=1000)
    max_cost_usd: float = Field(default=0.20, ge=0, le=100, allow_inf_nan=False)
    twitter_page_reserve: int = Field(default=100, ge=20, le=1000)


class Agent(Settings):
    command: list[str] = Field(default_factory=list)
    language: str = "English"
    timeout_seconds: int = Field(default=600, ge=1, le=1800)


class Config(Settings):
    run_dir: Path
    model: Model
    topic: str = Field(min_length=1, max_length=2000)
    twitter: Source = Field(default_factory=lambda: Source(enabled=False))
    xhs: XHSSource = Field(default_factory=lambda: XHSSource(enabled=False))
    budget: Budget = Field(default_factory=Budget)
    agent: Agent = Field(default_factory=Agent)
    min_score: float = Field(default=6, ge=0, le=10)
    max_enrich: int = Field(default=5, ge=0, le=100)
    comments: bool = False
    max_cards: int = Field(default=20, ge=1, le=100)
    max_payload_tokens: int = Field(default=6000, ge=1000, le=32000)

    @model_validator(mode="after")
    def validate_sources(self):
        if self.twitter.sort not in {"Latest", "Top"}:
            raise ValueError("twitter.sort must be Latest or Top")
        if self.xhs.sort not in {
            "general",
            "time_descending",
            "popularity_descending",
            "comment_descending",
            "collect_descending",
        }:
            raise ValueError("invalid xhs.sort")
        if any(not re.fullmatch(r"[A-Za-z0-9_]{1,15}", x) for x in self.twitter.watchlist):
            raise ValueError("twitter.watchlist requires account names without @")
        if self.xhs.watchlist:
            raise ValueError("xhs.watchlist is unsupported; use bounded keyword queries")
        return self


def config_dir() -> Path:
    xdg = Path(os.environ.get("XDG_CONFIG_HOME", ""))
    return (xdg if xdg.is_absolute() else Path.home() / ".config") / "social-digest"


def load_env(path: Path, env: dict[str, str]) -> None:
    if not path.exists():
        return
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("env file must be private (mode 0600)")
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
            raise ValueError(f"invalid env assignment at line {number}")
        env.setdefault(key.strip(), value.strip())


def load_config(path: Path) -> Config:
    with path.open("rb") as handle:
        config = Config.model_validate(tomllib.load(handle))
    config.run_dir = config.run_dir.expanduser()
    if not config.run_dir.is_absolute():
        raise ValueError("run_dir must be absolute")
    return config


Stage = Literal["collect", "score", "enrich", "evidence", "cards", "synthesize"]
