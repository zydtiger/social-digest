# social-digest

A standalone CLI that collects X/Twitter and Xiaohongshu, uses a local model for
relevance and traceable evidence, and passes a compact payload to an optional
agent for final Markdown synthesis.

## Setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and a running
OpenAI-compatible local-model endpoint. Final synthesis also requires a command
that reads a prompt from stdin and writes Markdown to stdout.

```sh
uv sync
mkdir -p ~/.config/social-digest
cp config.example.toml ~/.config/social-digest/config.toml
```

Set `model.base_url`, `model.model`, topic, source queries, and budgets. The
default config directory follows an absolute `XDG_CONFIG_HOME`. `--config PATH`
selects another config. There are no built-in personal endpoints or model names.

Put credentials in a private `env` file beside the config. The loader reads plain
`KEY=VALUE` lines, without shell expansion or quote processing. Existing process
variables take priority. Create it with mode 0600, for example using
`install -m 600 /dev/null ~/.config/social-digest/env`, then edit it:

```text
TWITTERAPI_API_KEY=YOUR_TWITTERAPI_IO_KEY
TIKHUB_API_KEY=YOUR_TIKHUB_KEY
```

These variables stay in the collector's process-local mapping. The final agent
receives an allowlisted environment without provider or ambient API keys. Agent
authentication must work through its own existing login or credential storage.

## Commands

```sh
uv run social-digest config check
uv run social-digest run --stage collect
uv run social-digest run --dry-run
uv run social-digest run
uv run social-digest run --since 2026-10-27 --until 2026-11-03 --dry-run
```

`--until` is exclusive, in UTC. By default the CLI covers the preceding complete
UTC day. A run directory is identified by its window. `--stage` executes through
that stage: `collect`, `score`, `enrich`, `evidence`, `cards`, or `synthesize`.
`--dry-run` stops at cards, or at an earlier explicit stage, before the paid agent.
It can still spend provider credits and call the local model.

Repeat the same command to resume. Completed requests and stages are reused.
Changing data-selection config or import inputs for the same window is rejected;
use another `run_dir` for a separate experiment. Budget caps and the optional
agent command can be changed without invalidating saved data. The CLI never
retries provider requests.
A request with uncertain delivery stops resume to prevent duplicate billing.
An agent attempt without a digest requires explicit `--retry-agent` to try again.

Manual daily collection and weekly synthesis can share saved collections:

```sh
uv run social-digest run --since 2026-10-27 --until 2026-10-28 --stage collect
uv run social-digest run --since 2026-10-28 --until 2026-10-29 --stage collect
uv run social-digest run --since 2026-10-27 --until 2026-11-03 \
  --input-run /ABSOLUTE/RUNS/2026-10-27_2026-10-28 \
  --input-run /ABSOLUTE/RUNS/2026-10-28_2026-10-29 --dry-run
```

`--input-run` replaces network collection with those completed collections and
deduplicates provider IDs. Selected enrichment can still fetch detail/comments.
Twitter watchlists accept account names; XHS collection accepts keyword queries.
XHS results are filtered by returned timestamp; the API's bounded search pages
are not an exhaustive historical search.

The run directory contains raw responses, normalized JSONL, model checkpoints,
verified excerpts with source offsets, event groups, `cards.json`, a paid-request
ledger, and optional `digest.md`. The final JSON line reports paths and reserved
provider cost. Reservations use $0.00015 per Twitter tweet, with a conservative
configurable page allowance, and $0.01 per TikHub request. Failed or uncertain
requests retain their reservation. Final-agent costs are governed by the chosen
agent command; it is invoked only once after cards are complete.
An empty verified-card set stops before paid synthesis.

Raw source artifacts and local model checkpoints remain on disk for resume.
`max_payload_tokens` conservatively bounds final prompt UTF-8 bytes, including
instructions and JSON, so payloads stay below that many byte-level tokens.

## Development

Install [prek](https://prek.j178.dev/) 0.4.14+ on the machine, then:

```sh
prek install
uv run pytest -q
prek run --all-files
prek run --all-files --hook-stage pre-push
```
