# 7.5. Releasing & version tags

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
2. **Review the license inventory** — on the release branch, run
   `uv run --project bootstrapper python -m scripts.docs.license_inventory --check`
   and re-read the [supply-chain license inventory](../reference/license-inventory.md)
   rows for every image and model this release adds or moves. A release that
   ships third-party artifacts, such as an appliance bundle or a prebuilt image
   set, is not published while an open item leaves any of them `unresolved`
   for redistribution.
3. **Promote through Gitflow** — merge the release branch into `develop` by
   pull request, then merge `develop` into `main` by pull request with every
   required check green. The versioned changelog and release notes must be on
   `main` before the tag is created.
4. **Create the tag** — update local `main`, verify that its changelog contains
   the version heading, then create and push the immutable annotated tag:

    ```bash
    git switch main
    git pull --ff-only
    git show HEAD:docs/CHANGELOG.md | grep -F "[X.Y.Z]"
    git tag -a vX.Y.Z -m "Atlas vX.Y.Z"
    git push origin vX.Y.Z
    ```

5. **Record immutable object IDs** — after the tag exists, capture both object
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

## 6. Release notes from Conventional Commits

`scripts/release_notes.py` answers the changelog-toil question in
[#968](https://github.com/thekaveh/atlas/issues/968). It reads a commit range
from the local repository, classifies each first-parent commit by its
Conventional Commits subject, and renders concise, grouped notes. It installs
no release framework, needs no credentials, and never publishes a version.

**Adopted contract (2026-09-17).** The `[Unreleased]` section of
`docs/CHANGELOG.md` opens with a generated block between the
`GENERATED RELEASE NOTES` markers. The block lists breaking changes, security
fixes, and features entry by entry and counts everything else; the curated
entries that follow it are the detailed history and are never touched by the
tool. The block records the exact range it was rendered from
(`<!-- generated-range: v0.1.0..<commit> -->`), so it is reproducible, and
`--check-changelog` (run by the *Bootstrapper and Backend suites* job, which the
required *Manifest lint + unit tests* gate requires, and by
`test_committed_changelog_block_is_current`) fails when the block differs from
what that range renders — a hand edit or a stale block is caught before merge.
Refresh it at release time, or whenever a summary of newer work is wanted:

```bash
uv run --project bootstrapper python -m scripts.release_notes --update-changelog --range v0.1.0..origin/develop
uv run --project bootstrapper python -m scripts.release_notes --check-changelog
```

Pull-request titles must be Conventional Commits subjects
(`type(scope)!: summary`; the required job runs
`scripts/release_notes.py --check-title`), because the squash commit takes its
subject from the title and the generator reads that subject. Accepted types:
`build`, `chore`, `ci`, `deps`, `docs`, `feat`, `fix`, `maintenance`, `perf`,
`refactor`, `release`, `security`, `style`, `test`. History before `v0.1.0`
keeps its hand-written form.

```bash
uv run --project bootstrapper python -m scripts.release_notes --range v0.1.0..origin/main
uv run --project bootstrapper python -m scripts.release_notes --since-tag --format json
uv run --project bootstrapper python -m scripts.release_notes --range A..B --overrides notes-overrides.yaml --output /tmp/notes.md
```

**How promotions are counted.** Gitflow lands every change twice: a squash
commit on `develop` and a promotion of `develop` into `main`. The generator
walks the range first-parent; a promotion *merge* is replaced by the `develop`
commits it carries, and every entry is de-duplicated by its last `(#PR)`
suffix. A promotion *squash* carries no `develop` commits, so it is folded into
the `develop` entries it promoted when its pull-request identity is known (see
the decision below) and otherwise stays a single *Promotions* entry. Subjects
that are not Conventional Commits are kept verbatim under *Unclassified* rather
than dropped.

**Numbering.** Stand-alone output is numbered `## 1.`, `## 2.`, … contiguously,
and the changelog block is always the first `### 1.1.` entry of the Unreleased
section, so both satisfy the heading contract `make docs-check` enforces on
hand-written pages; keep the block first, because the numbering tool would
otherwise rewrite its heading and the drift check would report it.

**Corrections.** A maintainer fixes a misclassified change with an overrides
file (`{pr: {bucket, subject}}`; a folded pair is keyed by its `develop` pull
request) passed on the command line. History is never rewritten, so the same
range renders the same notes for anyone who applies the same overrides.

**Decision: Atlas keeps its own generator (2026-10-04).** Atlas does not adopt
`semantic-release` (commit-analyzer, release-notes-generator, changelog, git)
and does not derive versions from commits, because of how Atlas releases:

- Tags are cut on `main` (§1), but `main` does not carry the changes' own
  commits: a release pull request squashes `develop` into one
  `chore(release): merge develop into main for #A and #B (#R)` commit. The
  commit-analyzer would read that `chore` and see neither the `feat`/`fix`
  types nor the breaking markers of what it carries, so it would pick the wrong
  version or none; on `develop`, where the typed squashes live, no tag is cut.
- Its changelog and git plugins commit the notes and push the tag from CI.
  `main` and `develop` accept changes only by pull request, tagging is a
  maintainer step followed by recording the immutable object IDs (§3, §4), and
  every Atlas workflow runs with a `contents: read` token, so publishing from
  CI would first need a write credential.
- About one subject in five since `v0.1.0` is not a Conventional Commits
  subject (the *Unclassified* entries), and the analyzer would skip it silently.
- What Atlas needed from it is already here: the generator is offline,
  credential-free, reproducible from a recorded range, gated by the required
  job, correctable by overrides, and its output meets the numbering contract.

MAJOR, MINOR, or PATCH stays the maintainer's call under §1, read off the
block's *Breaking changes* and *Features* lists. Nothing is published, the tag
procedure in §3 is unchanged, and the block is refreshed as part of its step 1
(finalize release notes).

**One released change per `develop` pull request.** A change is identified by
the pull request that squashed it into `develop`, the last `(#N)` of its
subject. Its promotion to `main` is the same change, never a second entry:

- A promotion merge is replaced by the `develop` commits it carries. Two
  release shapes are supported (#1351):
  - **Release merge:** a `release/<slug>-to-main` branch merges `develop` with
    `merge: bring develop (#N) into main` and lands by a merge-commit pull
    request. Any merge commit whose subject names `develop` then `main` is a
    promotion, whatever its type, so both merges are walked to the `develop`
    commits. While `main` still holds earlier squash promotions, a range on
    `main` lists those `develop` changes again, without a promotion marker,
    because they were never its ancestors.
  - **Release squash:** `chore(release): merge develop into main for #A and #B (#R)`.
- A release squash names its sources in its title (`… for #A and #B (#R)`), so
  `#R` is folded into each of those entries and rendered as `(#A, #R)`. A
  source outside the range is looked up by its `(#A)` suffix on `origin/develop`
  when that ref exists, otherwise on `develop`, and takes its own bucket and
  subject, unless it was released before the range starts. A source merged into
  `develop` by a merge commit carries no `(#A)` and is not found.
- The 22 earlier promotion squashes from #509 through #635 repeat the
  `develop` subject under their own number only, such as
  `feat(llm): … (#379) (#509)` for `feat(llm): … (#379) (#507)`.
  `REVIEWED_PROMOTIONS` in the script maps each one to its `develop` pull
  request. Every entry was checked against GitHub (the promotion's head commit
  is that pull request's squash commit) and Git (both commits make the same
  change to the same tree), and each pair renders as one entry, `(#507, #509)`.

Subjects are never matched: #1085 and #1136 carry the same subject and are two
different digest moves, so they stay two entries. A promotion stays one
*Promotions* entry only when a source is neither in the range nor found on
`develop`. The changelog block is rendered from a `develop` range, where each
change lands exactly once. A future promotion that cannot
name its sources gets a reviewed `REVIEWED_PROMOTIONS` entry, not a subject
rule.

**History before `v0.1.0`.** The generator never reads before `v0.1.0`. The
earlier entries, including the `1.0.0` through `3.0.0` milestone labels (§5),
stay hand-written as they are, and the curated entries below the block remain
the detailed history.
