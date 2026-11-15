# Optional local authorization evaluation

Follow [Development §6](../../docs/development.md#6-optional-local-authorization-evaluation) for the supported macOS ARM64 setup, automatic containment qualification, four-case walkthrough and result interpretation.

This optional recipe uses synthetic identities and local files. It changes no default Atlas dependency, service or image. Generated environments, profiles and reports belong in a private workspace outside this directory.

## 1. Accepted npm advisories

The lock keeps Promptfoo 0.124.1. Promptfoo 0.125.1, the newest release, still pins vulnerable children. npm reports nine findings: seven GHSA advisories on basic-ftp, minimatch, glob and @hono/node-server, and the packages that depend on them. Clearing them needs npm overrides that break Promptfoo's declared ranges. Those overrides cannot be verified here until the macOS qualification passes (#2029).

The owner accepted these advisories for this optional example on 2026-11-15 (#1894). The example is not in any shipped image. Its lock is outside the runtime-lock audit, and no CI job runs npm audit on it.
