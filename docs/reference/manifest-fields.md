# 10.6. Manifest Fields

## 1. Manifest Schema Quick Reference

Generated manifest schema quick reference.

| Field | Required | Purpose |
| --- | --- | --- |
| virtual | no | If true, this manifest has no compose fragment |
| name | yes | Folder name under `services/`, in kebab-case |
| label | yes | Human-readable label shown in the TUI wizard |
| category | yes | Topology category and wizard grouping |
| doc_extras | no | Optional doc-generation hints |
| data_flow | no | Runtime call graph (`data_flow.calls`) used by docs and diagrams |
| support | no | Support tier (`stable`, `experimental`, `community`, `unsupported`), the evidence it rests on, the release tag or commit that evidence was gathered at, the owner, and known limitations |
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
| runtime_sc | no | Per-source runtime scale/env/deploy slices |
| runtime_adaptive | no | Per-container adaptive-service descriptors |
| runtime_deps | no | Slice of the legacy service-configs.yml `service_dependencies:` block |
| runtime_dependency_tiers | no | Stack-wide startup tier order (the `globals` manifest only) |
| rows | no | Box rows this manifest renders |
