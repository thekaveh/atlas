# 8.5. Releasing & version tags

Atlas is consumed as a repository (see [Reusing Atlas as Infrastructure](reusing-atlas.md)), so downstream projects — especially those vendoring it as a Git submodule — need stable points to pin to and upgrade from deliberately. This page defines the tag convention.

## 1. Convention

- Tags are **semantic versions**: `vMAJOR.MINOR.PATCH` (e.g. `v0.1.0`), cut on `main`.
- **MAJOR** — breaking changes to the reuse/customization contract: the `*_SOURCE` set, `PROJECT_NAME`/`BASE_PORT` semantics, the shared network name, in-network service addresses, or the `services/_user/` overlay contract.
- **MINOR** — new services or capabilities, backward-compatible.
- **PATCH** — fixes, image pin bumps, docs.

`main` stays the rolling tip; tags are the pinnable checkpoints.

## 2. Pinning from a submodule consumer

```bash
# add (or move) the submodule to a tag
git -C infra fetch --tags
git -C infra checkout v0.1.0
git add infra && git commit -m "infra: pin Atlas to v0.1.0"

# later, upgrade deliberately
git -C infra fetch --tags && git -C infra checkout v0.2.0
git add infra && git commit -m "infra: bump Atlas v0.1.0 -> v0.2.0"
```

Pin to a **tag** (not `main`) so an infra upgrade is an explicit, reviewable commit in your project. A commit SHA also works if you need a point between tags.

## 3. Cutting a release (maintainer)

```bash
git checkout main && git pull --ff-only
git tag -a vX.Y.Z -m "Atlas vX.Y.Z"
git push origin vX.Y.Z
```

Add a dated heading in [CHANGELOG.md](../CHANGELOG.md) summarizing what changed since the previous tag.

## 4. History

- `v0.1.0` — first tagged checkpoint. Establishes the reuse contract (standalone shared-network + submodule consumption, `services/_user/` overlay auto-launch, `PROJECT_NAME`/`BASE_PORT`/`BRAND_*`/`*_SOURCE` customization) and the Phase 0 production-hardening profile.
