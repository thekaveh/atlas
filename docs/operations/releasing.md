# 7.5. Releasing & version tags

Downstream projects consume Atlas as a repository, often as a Git submodule ([Reusing Atlas as Infrastructure](reusing-atlas.md)). They need stable points to pin to and to upgrade from. This page defines the tag convention and the release procedure.

## 1. Convention

- Tags are **semantic versions**: `vMAJOR.MINOR.PATCH` (e.g. `v0.1.0`), cut on `main`.
- **MAJOR** — breaking changes to the reuse and customization contract. The contract is the `*_SOURCE` set, `PROJECT_NAME`/`BASE_PORT` semantics, the shared network name and in-network service addresses. It also covers the `atlas.consumer.yml` schema and the `services/_user/` overlay contract.
- **MINOR** — new services or capabilities, backward-compatible.
- **PATCH** — fixes, image pin bumps, docs.

`main` stays the rolling tip; tags are the pinnable checkpoints.

A release tag pushed to `origin` is immutable: do not move, delete or reuse
it. To correct a released checkpoint, cut a new semantic-version tag. A tag name
then always names one annotated tag object and one commit, so submodule pins
stay reproducible.

## 2. Pinning from a submodule consumer

```bash
# add (or move) the submodule to a tag or a reviewed main commit
git -C infra fetch --tags origin
git -C infra checkout <tag-or-reviewed-main-sha>
git add infra && git commit -m "infra: pin Atlas to <tag-or-sha>"

# later, upgrade deliberately
git -C infra fetch --tags origin && git -C infra checkout <new-tag-or-sha>
git add infra && git commit -m "infra: bump Atlas to <new-tag-or-sha>"
```

Pin to a fixed point, not to the moving `main` branch, so each infra upgrade is
an explicit, reviewable commit in your project. Use a tag when one covers the
features you need, or a commit SHA between tags.

The only release tag is `v0.1.0` (June 2026); the repository's other tags are
not releases. `v0.1.0` predates the consumer manifest (`atlas.consumer.yml`,
`--consumer`, `doctor`, `endpoints`). Until a newer tag is cut, pin a reviewed
`main` commit.

## 3. Cutting a release (maintainer)

1. **Finalize release notes** — on the release branch, move the relevant
   `[Unreleased]` material under a dated, linked `[X.Y.Z]` changelog heading.
   Review the complete notes that the tag will contain.
2. **Review the license inventory** — on the release branch, run
   `uv run --project bootstrapper python -m scripts.docs.license_inventory --check`.
   Re-read the [supply-chain license inventory](../reference/license-inventory.md)
   rows for every image and model this release adds or moves. Some releases ship
   third-party artifacts, such as an appliance bundle or a prebuilt image set.
   Do not publish one while an open item leaves any artifact `unresolved` for
   redistribution.
3. **Promote through Gitflow** — merge the release-notes branch into `develop`
   by pull request. Then cut `release/<slug>-to-main` from `origin/main` and
   merge `origin/develop` into it. Land it on `main` by a merge-commit pull
   request with every required check green. The versioned changelog and
   release notes must be on `main` before the tag is created.
4. **Create the tag** — update local `main` and verify that its changelog
   contains the version heading. Then create and push the immutable annotated
   tag:

    ```bash
    git switch main
    git pull --ff-only
    git show HEAD:docs/CHANGELOG.md | grep -F "[X.Y.Z]"
    git tag -a vX.Y.Z -m "Atlas vX.Y.Z"
    git push origin vX.Y.Z
    ```

5. **Record immutable object IDs** — after the tag exists, capture both object
   IDs and the tag date. Add a row to the record below on a follow-up branch.
   Promote that branch through `develop` and `main` by pull request:

    ```bash
    git rev-parse refs/tags/vX.Y.Z
    git rev-parse 'refs/tags/vX.Y.Z^{}'
    git for-each-ref --format='%(creatordate:short)' refs/tags/vX.Y.Z
    ```

The record is updated after tagging because an annotated tag object's SHA
does not exist until the tag is created. The release-history guard therefore
permits exactly one brief automatic transition state. One unrecorded linked
release heading is allowed while it is the newest classified history entry and
its matching `vX.Y.Z` tag does not yet exist. There is no separate "pending"
heading label, so the tagged tree contains the final release heading.

When the tag exists, the full-history CI checkout fails until the follow-up pull
request records the object IDs. Every other unrecorded heading fails. A missing,
extra, malformed, lightweight or moved release tag also fails.

## 4. Immutable release record

This is the canonical offline record that the documentation test reads. `Tag
object` is the annotated tag object's SHA. `Target commit` is its peeled commit
SHA. `Target changelog` records whether the tagged tree contains its own
versioned heading. The test also checks the markers and the table structure.

<!-- atlas-release-record:start -->
| Tag | Tagged | Tag object | Target commit | Target changelog |
| --- | --- | --- | --- | --- |
| `v0.1.0` | `2026-06-21` | `e894bc9db328af4801981b5b54f84a1949f1077f` | `12f32135850731bbbe7d0cd4aa3ff5f1783f4387` | `legacy-unreleased-exception` |
<!-- atlas-release-record:end -->

## 5. History and numbering reset

- `v0.1.0` is Atlas's first release-style tag. It establishes the reuse contract: standalone shared-network and submodule consumption, `services/_user/` overlay auto-launch, and `PROJECT_NAME`/`BASE_PORT`/`BRAND_*`/`*_SOURCE` customization. It also includes the Phase 0 production-hardening profile.
- `v0.1.0` is the only target-changelog exception. Its tagged tree kept the checkpoint's changes under `[Unreleased]`, and a later history reconciliation added the versioned `0.1.0` heading. Every later tag must contain its own dated release heading before it is created.
- The older changelog labels `1.0.0`, `1.5.0`, `2.0.0` and `3.0.0` predate the tag convention. They name historical project milestones, not Git tags or published releases. The semantic-version tag line starts at `v0.1.0`; the earlier labels stay in the changelog to keep their chronology and content.

## 6. Release notes from Conventional Commits

`scripts/release_notes.py` renders release notes from a local commit range. It
classifies each first-parent commit by its Conventional Commits subject. It
installs no release framework, needs no credentials and publishes nothing.

**Changelog block.** The `[Unreleased]` section of `docs/CHANGELOG.md` starts
with a generated block between the `GENERATED RELEASE NOTES` markers. The block
lists breaking changes, security fixes and features one by one, and counts the
other entries. The curated entries below the block are the detailed history;
the tool never changes them.

The block records its range (`<!-- generated-range: v0.1.0..<commit> -->`), so
it is reproducible. `--check-changelog` fails when the block differs from what
that range renders. The *Bootstrapper and Backend suites* job (part of the
required *Manifest lint + unit tests* gate) and
`test_committed_changelog_block_is_current` run it. Refresh the block at release
time (§3 step 1), or when you want a summary of newer work:

```bash
uv run --project bootstrapper python -m scripts.release_notes --update-changelog --range v0.1.0..origin/develop
uv run --project bootstrapper python -m scripts.release_notes --check-changelog
```

Keep the block as the first `### 1.1.` entry of `[Unreleased]`. Otherwise the
numbering tool rewrites its heading and `make docs-check` reports drift.
Stand-alone output is numbered `## 1.`, `## 2.`, … without gaps.

**Pull-request titles.** Pull-request titles must be Conventional Commits
subjects (`type(scope)!: summary`); the required job runs
`scripts/release_notes.py --check-title`. A multi-commit pull request squashes
under its title. A single-commit pull request lands its commit subject instead,
which the gate does not check, so that subject must also be Conventional. A
title edited after the check ran is not checked again
([#1457](https://github.com/thekaveh/atlas/issues/1457)).

Accepted types: `build`, `chore`, `ci`, `deps`, `docs`, `feat`, `fix`,
`maintenance`, `perf`, `refactor`, `release`, `security`, `style`, `test`.
Subjects that are not Conventional Commits appear verbatim under
*Unclassified*. About one subject in five since `v0.1.0` is unclassified.

```bash
uv run --project bootstrapper python -m scripts.release_notes --range v0.1.0..origin/main
uv run --project bootstrapper python -m scripts.release_notes --since-tag --format json
uv run --project bootstrapper python -m scripts.release_notes --range A..B --overrides notes-overrides.yaml --output /tmp/notes.md
```

**Promotions.** Each change appears once, under the `develop` pull request that
squashed it (the last `(#N)` of its subject). Entries are de-duplicated by that
number; subjects are never matched.

- A promotion merge (a merge commit whose subject names `develop`, then `main`)
  is replaced by the `develop` commits it carries. Release branches
  (`release/<slug>-to-main`, landed by a merge-commit pull request) use this
  shape.
- A release squash (`chore(release): merge develop into main for #A and #B (#R)`)
  is folded into the entries it names and rendered as `(#A, #R)`. A named
  source outside the range is looked up on `origin/develop`, else `develop`,
  unless it was released before the range starts.
- Older promotion squashes that repeat a `develop` subject without its number
  are mapped in `REVIEWED_PROMOTIONS` in the script. A future promotion that
  cannot name its sources needs a reviewed entry there, not a subject rule.
- A promotion stays one *Promotions* entry only when a source is neither in the
  range nor found on `develop`. A source merged into `develop` by a merge commit
  has no `(#A)` and is not found.

The changelog block is rendered from a `develop` range. A range on `main` can
list some `develop` changes again, because `main` still holds earlier squash
promotions whose sources are not its ancestors.

**Corrections.** To fix a misclassified entry, pass an overrides file
(`--overrides`, `{pr: {bucket, subject}}`, keyed by the `develop` pull request).
History is never rewritten, so the same range and overrides always render the
same notes.

**Version choice.** Atlas does not use `semantic-release` and does not derive
versions from commits. MAJOR, MINOR or PATCH stays the maintainer's decision
under §1, based on the block's *Breaking changes* and *Features* lists. The
reasons:

- Tags are cut on `main`, but `main`'s first-parent history holds only
  promotions: release merges and older `chore(release)` squashes. A commit analyzer that reads first-parent subjects sees neither the
  `feat`/`fix` types nor the breaking markers of the changes they carry. On
  `develop`, where the typed squashes are, no tag is cut.
- `main` and `develop` accept changes only by pull request, and every Atlas
  workflow runs with a `contents: read` token. Publishing notes or tags from CI
  would need a write credential.
- An analyzer skips unclassified subjects without a warning.

**Before `v0.1.0`.** The generator never reads before `v0.1.0`. The earlier
entries, including the `1.0.0`–`3.0.0` milestone labels (§5), stay hand-written.
