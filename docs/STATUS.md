# Status

Updated 2026-11-03.

| Area | Current state | Evidence |
| --- | --- | --- |
| Implementation | Standalone staged CLI with bounded provider collection and local evidence processing | [architecture](ARCHITECTURE.md) |
| Validation | 38 offline tests, both hook stages, and package build pass | [tests](../tests/) and [hook config](../.pre-commit-config.yaml) |
| Obsidian export | Cached E2E digest exports to a weekly Social Digest with up/tags/week/reported frontmatter and an evidence table; obsidian-cli confirms the parent backlink | [local write checkpoint](../runs/e2e/2026-10-27_2026-11-04/write.json), local-only evidence |
| Providers | Live collection and enrichment use 4 requests with $0.05 reserved cost | [local ledger](../runs/e2e/2026-10-27_2026-11-04/requests.json), local-only evidence |
| End to end | 40 collected items produce 3 cards and a final digest; the prompt is 3,415 UTF-8 bytes | [local cards](../runs/e2e/2026-10-27_2026-11-04/cards.json), [local digest](../runs/e2e/2026-10-27_2026-11-04/digest.md), local-only evidence |
| Synthesis | The configured agent completes in one attempt | [local agent checkpoint](../runs/e2e/2026-10-27_2026-11-04/agent.json), local-only evidence |
| Resume | Repeated write preserves exact note, parent, and write checkpoint bytes; request ledger, agent checkpoint, digest, and run manifest SHA256 values stay unchanged, with zero extra paid calls | Same local E2E artifacts |
| Credentials | Private env loading is configured with mode 0600; matching Keychain entries are removed | Local runtime configuration |
| Deployment | Manual invocation only; no scheduler or system configuration | [usage](../README.md) |

## Unverified

- XHS video detail uses the documented endpoint but has no live video fixture.
