# Status

Updated 2026-11-09.

| Area | Current state | Evidence |
| --- | --- | --- |
| Implementation | Standalone staged CLI with bounded provider collection and local evidence processing | [architecture](ARCHITECTURE.md) |
| Validation | 49 offline tests, both hook stages, and package build pass | [tests](../tests/) and [hook config](../.pre-commit-config.yaml) |
| Weekly runs | `run --weekly` covers seven complete UTC days and rejects explicit date combinations | [CLI tests](../tests/test_cli.py) |
| Notifications | Final JSON retains absolute note paths and adds a vault-relative notification object | [CLI tests](../tests/test_cli.py) |
| Obsidian export | Weekly notes include frontmatter, an evidence table, and a parent backlink; completed resume preserves edits | [note tests](../tests/test_note.py) |
| Providers | Bounded collection and enrichment use durable cost reservations and response caches | [pipeline tests](../tests/test_pipeline.py), [provider contracts](ARCHITECTURE.md#provider-contracts) |
| Synthesis | A configured command receives bounded evidence and returns the digest; failed attempts require explicit retry | [agent implementation](../src/social_digest/agent.py), [pipeline tests](../tests/test_pipeline.py) |
| Resume | Cached paid stages survive output configuration changes; note-write retries do not repeat synthesis | [note tests](../tests/test_note.py) |
| Credentials | Private env files and process variables are supported; provider keys are excluded from the final agent environment | [setup](../README.md#setup), [pipeline tests](../tests/test_pipeline.py) |
| Deployment | CLI supports external schedulers; no scheduler configuration is bundled | [usage](../README.md) |
| XHS video detail | Live responses list notes directly under `data.data`, without the image `note_list` wrapper | [pipeline tests](../tests/test_pipeline.py) |
