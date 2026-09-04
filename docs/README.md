# 9.3. Atlas Documentation

Documentation index for Atlas.

## 1. Documentation structure

### 1.1. Quick Start guides
- [Atlas documentation home](index.md) — overview and entry point for the complete documentation set
- [Quick Start](quick-start/index.md) — launch commands, common paths, and the first services to visit
- [Interactive Setup Wizard](quick-start/interactive-setup-wizard.md) — step-by-step guided configuration
- [Troubleshooting](quick-start/troubleshooting.md) — common issues and solutions across the full stack
- [Startup Troubleshooting](TROUBLESHOOTING.md) — quick fixes for first-launch errors (sudo recovery, Airflow ResolutionImpossible, n8n restart-loops); linked from `start.sh`'s own error output
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
- [Architecture catalog](architecture/index.md) — generated index of platform, lifecycle, data-flow, routing, observability, and security perspectives
- [Diagram authoring](architecture/README.md) — regeneration workflow and source-of-truth rules for architecture assets
- [Diagram asset catalog](diagrams/README.md) — top-level diagram update workflow and per-service auto-generation chain
- The top-level diagram itself lives at [diagrams/architecture.svg](diagrams/architecture.svg) (embedded in the project README) and [diagrams/architecture.html](diagrams/architecture.html) (standalone view)

### 1.5. Configuration and operations
- [Configuration overview](configuration.md) — environment files, SOURCE overrides, and base-port behavior
- [Operations overview](operations.md) — runtime commands, automation, validation, health, and managed-host lifecycle
- [SOURCE Configuration](deployment/source-configuration.md) — SOURCE-based deployment, including GPU variants
- [Ports and Routes](deployment/ports-and-routes.md) — canonical port offsets, direct URLs, and Kong routes
- [Iceberg advanced smoke test](deployment/iceberg-advanced-smoke.md) — opt-in validation for write, schema, snapshot, time-travel, and maintenance behavior
- [Reusing Atlas as Infrastructure](deployment/reusing-atlas.md) — overview + decision guide: use Atlas as the backing infra for another project (which method, is it ready, how to wire + customize)
- [Using as a Submodule](deployment/submodule-usage.md) — deep-dive for the Git-submodule reuse method
- [Releasing & version tags](deployment/releasing.md) — semver tag convention for pinning a vendored Atlas
- [Expected Startup Warnings](deployment/expected-startup-warnings.md) — known-benign log lines on `./start.sh`

### 1.6. Development and contribution
- [Development overview](development.md) — service admission, consumer layout, required checks, and repository structure
- [Adding a service runbook](CONTRIBUTING-services.md) — six-decision walkthrough + the regen + lint chain
- [Security policy](../SECURITY.md) — threat tiers, supported versions, responsible-disclosure address
- [External dependency contract ledger](maintenance/external-contract-ledger.md) — durable record of consumed external API/CLI/config contract checks from maintenance passes

### 1.7. Cross-service research (Phase B corpus)
- [Research corpus guide](research/README.md) — layout, authoring rules, and the schema the validator enforces
- [Integration matrix](research/integration-matrix.md) — auto-generated index linking every service to its candidate integrations
- [Per-service rows](research/rows/) — missing-pair integrations, candidate new services, per-service feature gaps
- [Candidate one-pagers](research/candidates/) — design notes per candidate service

### 1.8. Feature-track plans and specs
<!-- BEGIN GENERATED PLAN ARCHIVE RANGE -->
- [superpowers/plans](superpowers/plans/) + [superpowers/specs](superpowers/specs/) — point-in-time implementation plans and specs dated 2026-05-31 through 2026-08-30 (consult them when archaeology on a past track is needed; CHANGELOG entries link the relevant artifacts)
<!-- END GENERATED PLAN ARCHIVE RANGE -->

### 1.9. Numbering-policy notes
- Generated research files keep schema-fixed headings such as `## Headline`; see [research/README.md](research/README.md) for the explicit exemption.
- Provider implementation notes under `services/*/provider/` are operational backend-specific runbooks. They may keep compact unnumbered headings when numbering would make command-oriented maintenance notes harder to scan.
- Conventional history/planning artifacts such as [CHANGELOG](CHANGELOG.md), [ROADMAP](ROADMAP.md), and `docs/plans/` may keep their established release-note or planning heading style when renumbering would obscure chronology.
- Literal UI/output glyphs may appear only when the documentation is naming an actual terminal control, status marker, tree connector, or generated output. Do not use glyphs as decorative prose. Prefer words such as `Warning:` in explanatory text, and keep flow arrows or tree characters inside technical notation or literal examples.

## 2. Related documentation

- [Main README](../README.md) — project overview and quick start
- [Reference index](reference/index.md) — generated SOURCE, environment, port, dependency, and manifest-field references
- [ROADMAP](ROADMAP.md) — future development plans
- [CHANGELOG](CHANGELOG.md) — release history and completed features

## 3. Getting help

If you can't find what you're looking for:

1. Check the [Troubleshooting Guide](quick-start/troubleshooting.md)
2. Search through the service-specific documentation
3. Open an issue on GitHub if you need additional help

## 4. Contributing to documentation

- Found a typo or error? Open a PR.
- Missing information? Open an issue.
- Before submitting documentation changes, run the single root-safe gate: `make docs-check`.
