# 10.6. Manifest Fields

## 1. Manifest Schema Quick Reference

Generated from the top-level keys of `bootstrapper/schemas/service.schema.json`. See [Contributing §18](../CONTRIBUTING-services.md#18-schema-cheatsheet) for nested fields.

Schema-required marks the keys the JSON schema requires. The validator and tests also require `support`, `containers` on non-virtual manifests (matching `compose.yml`), and `docs`, a sibling `README.md` or `docs_exception`.

| Field | Schema-required | Purpose |
| --- | --- | --- |
| virtual | no | If true, this manifest has no compose fragment |
| name | yes | Folder name under `services/`, in kebab-case |
| label | yes | Human-readable label shown in the TUI wizard |
| category | yes | Topology category and wizard grouping |
| doc_extras | no | Optional doc-generation hints |
| data_flow | no | Runtime call graph (`data_flow.calls`) used by docs and diagrams |
| support | no | Support tier (`stable`, `experimental`, `community`, `unsupported`), its evidence and the tag or commit it was gathered at, the owner, and known limitations |
| capabilities | yes | Required operator-facing capability and limitation contract |
| docs | no | Repository-relative operator documentation path |
| docs_exception | no | Printable reason with an explicit `because` clause, four substantive words, and three distinct terms |
| extra_kong_aliases | no | Extra `*.localhost` Kong hostnames beyond each row's alias |
| containers | no | Container names in the service family |
| images | no | Image env var, pinned default image and the container that uses it |
| sources | no | SOURCE var, default, and allowed values |
| env | yes | Environment variables owned by the manifest |
| depends_on | no | Required and optional logical dependencies |
| exports | no | Documents the cross-service env-var contract |
| runtime_sc | no | Per-source runtime scale/environment/deploy/extra_hosts slices |
| runtime_adaptive | no | Per-container adaptive-service descriptors |
| runtime_deps | no | Launch-time dependency rules (requires / optional / conditional_requires); a missing requirement auto-disables the service |
| runtime_dependency_tiers | no | Stack-wide startup tier order (the `globals` manifest only) |
| rows | no | Box rows this manifest renders |
