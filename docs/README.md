# 9.3. Documentation Map

This page indexes the Atlas documentation. Start from the path that matches your role:

- **New to Atlas (run it locally):** [Documentation home](index.md) → [Quick Start](quick-start/index.md) → [Interactive Setup Wizard](quick-start/interactive-setup-wizard.md) → [Core Concepts](core-concepts.md) → [Tracks](tracks.md) → [Quick Start Troubleshooting](quick-start/troubleshooting.md).
- **Building on Atlas (application developer):** [Service catalog](services.md) → [Configuration](configuration.md) → [Reusing Atlas as Infrastructure](operations/reusing-atlas.md) → [Endpoint contract export](operations/index.md#5-endpoint-contract-export) → [Backend plugin manifest](operations/index.md#6-backend-plugin-manifest).
- **Operating or contributing:** [Operations](operations/index.md) → [Architecture](architecture/index.md) → [Development](development.md) → [Contributing guide](../CONTRIBUTING.md) → [Reference](reference/index.md) → [Releasing](operations/releasing.md) → [Security policy](../SECURITY.md).

## 1. Documentation structure

### 1.1. Getting started
- [Atlas documentation home](index.md) — overview and entry point for the complete documentation set
- [Quick Start](quick-start/index.md) — launch commands, common paths, and the first services to visit
- [Interactive Setup Wizard](quick-start/interactive-setup-wizard.md) — step-by-step guided configuration
- [Quick Start Troubleshooting](quick-start/troubleshooting.md) — common problems and fixes across the full stack, including recovery and reset
- [Sudo Recovery](TROUBLESHOOTING.md) — recovery after a launch or stop under `sudo`; linked from the `start.sh` and `stop.sh` error output
- [Core concepts](core-concepts.md) — SOURCE values, tracks, adaptive services, and routing
- [Tracks](tracks.md) — generated track-to-service matrix and selection behavior

### 1.2. Service documentation
- [Service catalog](services.md) — manifest-derived inventory of every service family, SOURCE variant, track, and dependency
- [Service directory layout](../services/README.md) — folder ownership, virtual manifests, and service admission conventions

### 1.3. Provider and extension guides
- [Docling Localhost Provider](../services/docling/provider/localhost/README.md) — run document extraction through a host-managed Docling process
- [Parakeet Provider Overview](../services/parakeet/provider/README.md) — choose among supported speech-to-text provider backends
- [Parakeet MLX Provider](../services/parakeet/provider/mlx/README.md) — Apple Silicon-native Parakeet setup and operation
- [Parakeet whisper.cpp Provider](../services/parakeet/provider/whisper-cpp/README.md) — whisper.cpp-compatible speech-to-text operation
- [TTS Provider Overview](../services/tts-provider/provider/README.md) — select and configure the text-to-speech provider family
- [TTS Localhost Provider](../services/tts-provider/provider/localhost/README.md) — connect Atlas to a host-managed TTS process
- [User Supabase Migrations](../services/supabase/db/_user/README.md) — add downstream-owned SQL after Atlas migrations

### 1.4. Architecture diagrams
- [Platform architecture](architecture/index.md) — the top-level platform diagram
- [Diagram catalog](architecture/README.md) — generated index of the platform, lifecycle, data-flow, routing, observability, and security perspectives
- [Diagram authoring](diagrams/README.md) — top-level diagram update workflow and per-service auto-generation chain

### 1.5. Configuration and operations
- [Configuration overview](configuration.md) — environment files, SOURCE overrides, and base-port behavior
- [Operations overview](operations/index.md) — runtime commands, automation, validation, health, and managed-host lifecycle
- [SOURCE Configuration](operations/source-configuration.md) — SOURCE-based deployment, including GPU variants
- [Ports and Routes](operations/ports-and-routes.md) — canonical port offsets, direct URLs, and Kong routes
- [Access and Credentials](operations/access-and-credentials.md) — which credential opens each surface, and what Supabase identity does and does not cover
- [Iceberg advanced smoke test](operations/iceberg-advanced-smoke.md) — opt-in validation for write, schema, snapshot, time-travel, and maintenance behavior
- [Reusing Atlas as Infrastructure](operations/reusing-atlas.md) — decision guide for using Atlas as another project's infrastructure: which method, readiness, wiring and customization
- [Using as a Submodule](operations/submodule-usage.md) — deep-dive for the Git-submodule reuse method
- [Releasing & version tags](operations/releasing.md) — semver tag convention for pinning a vendored Atlas
- [Expected Startup Warnings](operations/expected-startup-warnings.md) — known-benign log lines on `./start.sh`

### 1.6. Development and contribution
- [Contributing guide](../CONTRIBUTING.md) — first contribution: setup, one safe Backend and bootstrapper test, the Docker and live-test boundary, the `develop` pull-request target, and the four required checks
- [Development overview](development.md) — service admission, consumer layout, required checks, and repository structure
- [Adding a service runbook](CONTRIBUTING-services.md) — six-decision walkthrough + the regen + lint chain
- [Security policy](../SECURITY.md) — project posture, operational tiers, reachability triage, public-edge requirements, automated scanning gates, and the private-advisory reporting route
- `docs/superpowers/` (repository only, not published) — point-in-time feature-track plans and specs
- `docs/research/`, `docs/strategy/` and `docs/maintenance/` (repository only, not published) — integration research, strategy notes and the external dependency contract ledger

## 2. Related documentation

- [Reference index](reference/index.md) — generated SOURCE, environment, port, dependency, and manifest-field references
- [Supply-chain license inventory](reference/license-inventory.md) — the license of every pinned image and downloaded model weight: notices, hosted-use, source-integration and redistribution terms, and open release review items
- [ROADMAP](ROADMAP.md) — future development plans
- [CHANGELOG](CHANGELOG.md) — release history and completed features

## 3. Getting help

If you can't find what you're looking for:

1. Check [Quick Start Troubleshooting](quick-start/troubleshooting.md)
2. Search through the service-specific documentation
3. Open a GitHub issue if you need additional help (use the `question` label for questions)
4. For security-sensitive findings, follow the [security policy](../SECURITY.md) instead of opening a public issue

## 4. Contributing to documentation

- Found a typo or error? Open a PR against `develop`; the [contributing guide](../CONTRIBUTING.md) has the steps.
- Missing information? Open an issue.
- Before you submit documentation changes, run `make docs-check` from the repository root. It validates all documentation surfaces.
