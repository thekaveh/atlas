# Wizard recovery guidance (#1029)

## 1. Verified problem

At `7a8e777b`, `WizardScreen._emit_failure_hints` recommends recursive
world-writable permissions for a generic bind-mount failure and asserts that
authentication errors prove a stale database volume. Neither inference is
supported by captured logs. The cold-stop action already requires two matching
key presses, but the pending confirmation outlives its eight-second toast.

## 2. Design

Keep recovery in the existing launch log and retain its palette, filters,
detachment, stop and cold-stop actions. Classify permission, authentication and
connection failures independently so concurrent failures do not hide each other.
Use fixed diagnostic prose, never interpolate untrusted log payloads into
commands. Known container paths may identify the affected mount; unknown
ownership must explicitly say that expected UID/GID and host mapping still need
verification. Do not infer root ownership from a permission error.

For authentication, explain three checks in order: service availability,
effective project/env configuration, then stored credentials. Recommend retaining
the volume and restoring matching configuration or following the service's
credential-recovery procedure with a backup. Do not suggest cold start as an
authentication repair. Preserve detach and retry of the original launch command.

Cold stop remains a separate `Ctrl+X` action. Before confirmation, the persistent
log and notification describe the target project, Compose-managed named and
attached anonymous volumes, and the database records, object files, histories,
models and caches held in those volumes. Explain that bind mounts, external
volumes, `.env`, and managed host processes remain. Two matching presses must
occur within eight seconds; an expired or different first press cannot authorize
deletion. Cold-start selection separately explains env/key regeneration.
Its warning opts into a wrapping subtitle, reclaiming decorative panel spacing
so the complete warning and choices fit together; other prompts retain their
existing one-line subtitle.

Alternatives rejected: changing one unsafe command leaves false diagnosis and
reset sequencing intact; automatic ownership/credential repair exceeds this
ticket and could mutate user state. No new diagnostic framework is needed.

## 3. Scope and verification

This changes TUI recovery guidance and TUI confirmation, not the explicitly
requested headless `--cold` contract or startup ownership-repair implementation.
Tests use synthetic logs and fake stoppers only. Cover unknown/known paths,
authentication and unavailable-service combinations, no secret echo, retry,
cold-stop scope/expiry and existing stop semantics. Render recovery with Textual
and retain before/after screenshots. Update the canonical wizard guide and run
the three-surface checks. Docker's `compose down --volumes` reference confirms
named/anonymous deletion and external-volume exclusion; no live teardown is
necessary for this guidance-only issue.

## 4. Delivery

Implement on the issue branch, verify and review, squash into `develop`, promote
`develop` to `main`, verify publication, then close #1029 and delete only the
campaign branch. Reverting the issue commit restores previous UI behavior;
there is no data migration or fixture mutation to reverse.
