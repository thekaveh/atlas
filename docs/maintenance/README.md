# Maintenance notes

This directory holds durable maintenance records referenced by the overnight
maintenance process.

## 1. Index

- [external-contract-ledger.md](./external-contract-ledger.md) — durable ledger of consumed external contracts, recording what was checked and the pinned or configured version.
- [integration-claims-ledger.md](./integration-claims-ledger.md) — sampled review of the manifests' `data_flow.calls` edges against the code and configuration that carry them, with a verdict per edge and the unsampled remainder by caller.
