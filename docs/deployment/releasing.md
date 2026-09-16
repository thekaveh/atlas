# 8.5. Releasing & version tags

Atlas is consumed as a repository (see [Reusing Atlas as Infrastructure](reusing-atlas.md)), so downstream projects — especially those vendoring it as a Git submodule — need stable points to pin to and upgrade from deliberately. This page defines the tag convention.

## 1. Convention

- Tags are **semantic versions**: `vMAJOR.MINOR.PATCH` (e.g. `v0.1.0`), cut on `main`.
- **MAJOR** — breaking changes to the reuse/customization contract: the `*_SOURCE` set, `PROJECT_NAME`/`BASE_PORT` semantics, the shared network name, in-network service addresses, or the `services/_user/` overlay contract.
- **MINOR** — new services or capabilities, backward-compatible.
- **PATCH** — fixes, image pin bumps, docs.

`main` stays the rolling tip; tags are the pinnable checkpoints.

Once a release tag has been pushed to `origin`, it is immutable: do not move,
delete, or reuse it. Correct a released checkpoint with a new semantic-version
tag. This preserves reproducible submodule pins and makes a tag name a durable
reference to one annotated tag object and one commit.

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

1. **Finalize release notes** — on the release branch, replace the relevant
   `[Unreleased]` material with a dated, linked `[X.Y.Z]` changelog heading and
   review the complete notes that the tag will contain.
2. **Promote through Gitflow** — merge the release branch into `develop` by
   pull request, then merge `develop` into `main` by pull request with every
   required check green. The versioned changelog and release notes must be on
   `main` before the tag is created.
3. **Create the tag** — update local `main`, verify that its changelog contains
   the version heading, then create and push the immutable annotated tag:

    ```bash
    git switch main
    git pull --ff-only
    git show HEAD:docs/CHANGELOG.md | grep -F "[X.Y.Z]"
    git tag -a vX.Y.Z -m "Atlas vX.Y.Z"
    git push origin vX.Y.Z
    ```

4. **Record immutable object IDs** — after the tag exists, capture both object
   IDs and its date, add a row to the record below on a follow-up branch, and
   promote that branch through `develop` and `main` by pull request:

    ```bash
    git rev-parse refs/tags/vX.Y.Z
    git rev-parse 'refs/tags/vX.Y.Z^{}'
    git for-each-ref --format='%(creatordate:short)' refs/tags/vX.Y.Z
    ```

The post-tag record update is necessary because an annotated tag object's SHA
does not exist until the tag is created. To make the two pull requests possible,
the release-history guard permits exactly one brief automatic transition state:
one unrecorded linked release heading is allowed only when it is the newest
classified history entry and its matching `vX.Y.Z` tag does not yet exist. This
does not use a separate “pending” heading label, so the tree that is tagged
contains the final release heading. Once the tag exists, the full-history CI
checkout rejects it until the follow-up pull request records its immutable
object IDs. The guard rejects every other unrecorded heading and any missing,
extra, malformed, lightweight, or moved release tag.

## 4. Immutable release record

This is the canonical, offline record used by the documentation test. `Tag
object` is the annotated tag object's SHA; `Target commit` is its peeled commit
SHA. `Target changelog` records whether that tagged tree contains its own
versioned heading. The markers and rendered table structure are part of the
test contract.

<!-- atlas-release-record:start -->
| Tag | Tagged | Tag object | Target commit | Target changelog |
| --- | --- | --- | --- | --- |
| `v0.1.0` | `2026-06-21` | `e894bc9db328af4801981b5b54f84a1949f1077f` | `12f32135850731bbbe7d0cd4aa3ff5f1783f4387` | `legacy-unreleased-exception` |
<!-- atlas-release-record:end -->

## 5. History and numbering reset

- [`v0.1.0`](https://github.com/thekaveh/atlas/tree/v0.1.0) is Atlas's first release-style tag. It establishes the reuse contract: standalone shared-network and submodule consumption, `services/_user/` overlay auto-launch, and `PROJECT_NAME`/`BASE_PORT`/`BRAND_*`/`*_SOURCE` customization, together with the Phase 0 production-hardening profile.
- `v0.1.0` is the sole historical target-changelog exception. Its tagged tree still placed the checkpoint's changes under `[Unreleased]`; the versioned `0.1.0` heading was added by the later history reconciliation. Every subsequent tag must contain its own dated release heading before it is created.
- The older changelog labels `1.0.0`, `1.5.0`, `2.0.0`, and `3.0.0` predate the tag convention. They identify historical project milestones, not Git tags or published releases. Atlas deliberately began its public semantic-version tag line at `v0.1.0`; the earlier labels remain in the changelog only to preserve their original chronology and content.

## 6. Release notes from Conventional Commits (prototype)

`scripts/release_notes.py` is the dry-run answer to the changelog-toil question
in [#968](https://github.com/thekaveh/atlas/issues/968). It reads a commit
range from the local repository, classifies each first-parent commit by its
Conventional Commits subject, and prints concise, grouped notes. It installs
no release framework, needs no credentials, never publishes a version, and
refuses to write `docs/CHANGELOG.md`.

```bash
uv run --project bootstrapper python -m scripts.release_notes --range v0.1.0..origin/main
uv run --project bootstrapper python -m scripts.release_notes --since-tag --format json
uv run --project bootstrapper python -m scripts.release_notes --range A..B --overrides notes-overrides.yaml --output /tmp/notes.md
```

**How promotions are counted.** Gitflow lands every change twice: a squash
commit on `develop` and a promotion of `develop` into `main`. The generator
walks `main` first-parent; a promotion *merge* is replaced by the `develop`
commits it carries, a promotion *squash* (`release: …`) stays as a single
*Promotions* entry, and every change is de-duplicated by its `(#PR)` suffix so
nothing is counted twice. Subjects that are not Conventional Commits are kept
verbatim under *Unclassified* rather than dropped.

**Numbering.** Rendered sections are numbered `## 1.`, `## 2.`, … contiguously,
so the output satisfies the same heading contract `make docs-check` enforces on
hand-written pages. That settles the compatibility question the issue raised:
generated notes can comply with the contract; whether `docs/CHANGELOG.md`
itself becomes generated (and therefore exempt from hand numbering) remains a
maintainer decision, and the file stays hand-maintained until it is made.

**Corrections.** A maintainer fixes a misclassified change with an overrides
file (`{pr: {bucket, subject}}`) passed on the command line. History is never
rewritten, so the same range renders the same notes for anyone who applies the
same overrides.

**Not done here.** No `semantic-release`, no version derivation, no CI gate on
commit-message format, and no published release; those follow only after the
decision above.
