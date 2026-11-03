# Architecture

The flow is `collect → score → enrich → evidence → cards → synthesize`. Each
stage has an atomic JSON checkpoint and list-shaped stages also write JSONL.
Local-model tasks cache individually, so interruption does not repeat finished
items. A nonblocking file lock prevents two writers for the same window.
Imported collection contents participate in the run signature; a changed source
collection cannot silently reuse an earlier weekly run's checkpoints.

## Cost boundary

Before dispatch, a durable ledger reserves a request and its conservative cost.
The raw response is written before the ledger is marked complete. Resume uses
the raw response even if interrupted between these writes. An entry without a
response is ambiguous and blocks automatic retry. A failed cached response
fails again without making another request. No provider HTTP retry is implicit.

Pagination, query count, watchlist size, and global request/cost limits bound
collection. Enrichment selects only top relevant items; exhausted enrichment
budgets retain search text and record the gap. Collection failure is explicit
and cannot silently become a complete digest.

The ledger reports reservations rather than a provider billing statement.
Twitter pages reserve a configurable upper allowance because returned counts
vary. The configured allowance is an assumption, not a server-side response cap.

## Evidence boundary

The local model scores text, then extracts claims with exact excerpts and source
identifiers. Only excerpts present in the supplied post/comment survive, with
start/end offsets. This verifies attribution, not factual truth. Cards carry
private raw-artifact paths and public source URLs. Compact cards preserve source
authors and publication dates; XHS URLs retain encoded search tokens when present.
Event clustering must form an
exact partition of card IDs; unknown, repeated, or missing IDs fail validation.

The final prompt uses only bounded evidence cards and coverage metadata. Its
UTF-8 byte length conservatively bounds token count for byte-level tokenizers.
Posts, comments, summaries, and cluster labels are untrusted input in every prompt.

## Agent boundary

Only synthesis invokes the optional agent command. It receives a minimal
environment and stdin payload. Provider credentials never enter its environment.
The command is expected to run without tools; the CLI does not enforce a sandbox.
An attempt marker precedes process launch; ambiguous or failed attempts need an
explicit retry flag. A durable digest makes subsequent synthesis a no-op.

## Provider contracts

- [TwitterAPI.io search](https://docs.twitterapi.io/api-reference/endpoint/tweet_advanced_search)
  uses `query`, `queryType`, and cursor pagination; `from:` constrains watchlists.
- [TikHub search](https://docs.tikhub.io/420136398e0) uses page and search session
  identifiers. Search timestamps enforce the requested UTC window locally.
- [TikHub comments](https://docs.tikhub.io/420136394e0) and note detail are fetched
  for selected items only. HTTP success does not override upstream failure codes.
