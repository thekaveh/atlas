# Temporary Build-Validation Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Temporarily remove the 20-minute image-build gate from Atlas pull requests without deleting it or weakening the other required checks.

**Architecture:** Gate the existing GitHub Actions job behind one repository variable and remove only its status context from the protected ruleset. Keep the workflow body intact so re-enabling requires configuration rather than reconstruction.

**Tech Stack:** GitHub Actions YAML, GitHub repository rulesets, `gh`, PyYAML

## 1. Global Constraints

- Keep strict required-status-check mode enabled.
- Preserve the three fast required checks.
- Do not change Dockerfiles, dependency pins, tests, or runtime behavior.

---

### 1.1. Task 1: Gate and document build validation

**Files:**
- Modify: `.github/workflows/services-lint.yml`
- Modify: `AGENTS.md`
- Create: `docs/superpowers/specs/2026-07-10-temporary-build-validation-gate-design.md`

- [x] Add `if: ${{ vars.ENABLE_BUILD_VALIDATION == 'true' }}` to `build-validation` without changing its steps.
- [x] Document the three required checks and the exact re-enable procedure.
- [x] Parse the workflow with PyYAML and assert the job gate value.
- [x] Run `git diff --check` and the three locally relevant CI checks.

### 1.2. Task 2: Update protection and merge

**Files:**
- External: GitHub ruleset `gitflow` (`18620077`)

- [x] Remove only `Build-validation (Dockerfile + requirements.txt installability)` from `required_status_checks`.
- [x] Verify strict mode and the other three contexts are unchanged.
- [ ] Push the branch and open a pull request to `main`.
- [ ] Verify Build-validation is skipped and all required checks pass.
- [ ] Squash-merge, delete the branch, and verify the merged workflow and live ruleset.
