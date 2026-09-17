# Bounded TUI Compose Reactivation (#1031) Implementation Plan

> **Status: archived plan** — dated 2026-09-14; a point-in-time record kept for archaeology, not current guidance. The landed outcome is recorded in the [CHANGELOG](../../CHANGELOG.md).

> **For agentic workers:** Execute sequentially in this session with TDD and a separate code review. The campaign directive authorizes implementation and both PR merges without another approval checkpoint.

**Goal:** Route the synchronous TUI Compose hook through the existing bounded async runner so n8n reactivation streams normally, times out, and cleans up its owned process tree on cancellation.

**Architecture:** A pipeline-scoped synchronous-to-async executor submits Compose commands from `asyncio.to_thread` to `_run_streamed_command` on the Textual loop. Its shielded thread-call method owns cancellation, awaits registered runner tasks and the worker thread, and then propagates cancellation.

**Tech Stack:** Python >=3.10, asyncio, concurrent futures, threading, Textual, pytest.

## 1. Global Constraints

- Preserve existing output, progress, colors, command construction, n8n health checks, and headless behavior.
- Use `_COMPOSE_UP_TIMEOUT_SECONDS` (30 minutes) for `restart n8n` and `_PROCESS_TERMINATION_GRACE_SECONDS` (2 seconds) for TERM-to-KILL cleanup.
- Treat the cleanup grace as the bound for an active Compose client process; preserve the separate existing 120-second n8n health-convergence window after a successful restart.
- Kill only the separate process group created for the submitted command; do not signal unrelated processes.
- Report timeout/cancellation/nonzero status without embedding argv, environment values, or captured subprocess output in the synthesized error line.
- Do not claim that client-process termination cancels work already accepted by the Docker daemon.
- Do not change Docker images, manifests, service architecture, protected branches, or user stacks.

---

## 2. Verified behavior and acceptance matrix

| Acceptance criterion | Planned change | Verification |
|---|---|---|
| Silent and never-exiting commands terminate by deadline plus cleanup grace | Submit the patched call to `_run_streamed_command` with `_compose_timeout_seconds(args)` | Real silent and endless Python subprocess fixtures with short monkey-patched deadline |
| Cancellation and timeout reap owned children only | Track bridge-owned asyncio tasks; latch cancellation before launch; await runner cleanup and worker completion | PID fixtures for leader/descendant plus an unrelated control process |
| Nonzero status fails with useful redacted diagnostics | Preserve integer return; add stable exit-status line without command/output interpolation | Exit-17 fixture and pipeline failure assertion with secret sentinel absent from synthesized diagnostic |
| Successful reactivation keeps streaming and behavior | Use existing classifier/sink and keep `AtlasStarter._reactivate_n8n_if_needed` contract | Multi-line exit-0 fixture and existing n8n reactivation/health tests |

## 3. File map

- Modify `bootstrapper/ui/textual/screens/wizard_screen.py`: add the pipeline-scoped executor, cancellation-aware thread boundary, and install it as the temporary Compose hook.
- Create `bootstrapper/tests/test_tui_compose_reactivation_1031.py`: controlled subprocess and actual-hook regression coverage.
- Modify `bootstrapper/tests/test_n8n_reactivation.py`: pass the executor through the TUI helper and verify failure/cancellation behavior.
- Modify this plan after verification with exact results and review findings.
- The design record is `docs/superpowers/specs/2026-09-14-tui-compose-reactivation-1031-design.md`.

## 4. Task 1: Specify the executor contract with failing process fixtures

**Files:**
- Create: `bootstrapper/tests/test_tui_compose_reactivation_1031.py`
- Test: `bootstrapper/tests/test_process_runner.py`

**Interfaces:**
- Consumes: `_run_streamed_command(command, *, cwd, env, on_line, timeout_seconds, termination_grace_seconds=...) -> int`.
- Produces: `_ThreadedComposeExecutor(manager, loop, log_line)` with synchronous `__call__(args, use_env_file=True, project_name=None) -> int`, `async run_in_thread(fn)`, and `async cancel_and_wait()`.

- [x] Write a real silent subprocess fixture and assert `__call__` returns 124 within the short configured deadline plus grace, with a timeout and exit-status diagnostic.
- [x] Write an endless-output fixture and assert progress arrives before the same bounded timeout.
- [x] Write a leader that spawns a TERM-resistant child. Assert timeout and cancellation stop both recorded PIDs, while a separately launched control process remains alive.
- [x] Write exit-17 and exit-0 fixtures. Assert the failure returns 17 and emits a stable numeric diagnostic; assert success preserves every streamed line and emits no failure line.
- [x] Run `uv run --project bootstrapper pytest bootstrapper/tests/test_tui_compose_reactivation_1031.py -q`; the initial RED run failed because `_ThreadedComposeExecutor` did not exist.

## 5. Task 2: Implement the smallest thread-to-loop bridge

**Files:**
- Modify: `bootstrapper/ui/textual/screens/wizard_screen.py`
- Test: `bootstrapper/tests/test_tui_compose_reactivation_1031.py`

**Interfaces:**
- `__call__` builds argv with `manager._build_compose_command(args, use_env_file=use_env_file, top_level_flags=["--ansi=never"])`, temporarily honoring `project_name`, then waits on `asyncio.run_coroutine_threadsafe`.
- `_run(command, args)` registers the current asyncio task, checks the cancellation latch before process launch, delegates to `_run_streamed_command`, and unregisters in `finally`.
- `run_in_thread(fn)` shields `asyncio.to_thread(fn)`; on cancellation it sets the latch, calls `cancel_and_wait`, awaits the thread task uncancellably, and re-raises the original cancellation.

- [x] Add the minimal executor with a lock-protected task set and `threading.Event` cancellation latch.
- [x] Classify each streamed Compose line through `_classify_compose_line` and pass it to the existing log callback.
- [x] Use `BUILDKIT_PROGRESS=plain`, the current manager root, timeout policy, and cleanup grace. Return 130 for the synchronous cancelled call, 1 for a launch/bridge exception, and the command return code otherwise.
- [x] Emit synthesized diagnostics as fixed text plus exception type or numeric status only.
- [x] Rerun the new suite; all nine executor fixtures pass and no fixture process survives cleanup.

## 6. Task 3: Install the executor on the actual TUI reactivation path

**Files:**
- Modify: `bootstrapper/ui/textual/screens/wizard_screen.py`
- Modify: `bootstrapper/tests/test_n8n_reactivation.py`
- Test: `bootstrapper/tests/test_tui_launch_exit_code.py`

**Interfaces:**
- `_reactivate_n8n_after_up(starter, compose_executor) -> bool` invokes `await compose_executor.run_in_thread(starter._reactivate_n8n_if_needed)`.
- `_run_pipeline_and_stream` installs the executor instance as `starter.docker_manager.execute_compose_command`, uses `run_in_thread` for pipeline steps that can reach that hook, and restores the original method in `finally`.

- [x] Add a failing helper test proving a reactivation nonzero result marks the launch failed through the executor seam.
- [x] Add a failing cancellation test proving `_reactivate_n8n_after_up` propagates `CancelledError` after executor cleanup.
- [x] Replace the raw `Popen` closure with the executor and route the steps loop plus n8n helper through its cancellation-aware thread method.
- [x] Preserve the existing command echo and all success-path log lines. Visual result: no success-state change; failures gain explicit bounded timeout/cancel/exit diagnostics in the existing Docker-colored log pane.
- [x] Run the executor, n8n, launch, and process-runner tests with warnings as errors; 158 passed after the final review fix.

## 7. Task 4: Regression, review, and delivery

**Files:**
- Modify: `docs/superpowers/plans/2026-09-14-tui-compose-reactivation-1031.md`
- Review: all files changed from `origin/develop`.

**Interfaces:**
- Produces: reviewed issue commit, develop PR, promotion PR, post-merge evidence, issue closeout, and clean campaign state.

- [x] Run the complete process-runner, n8n, managed-host lifecycle, TUI pipeline, launch-exit, and Docker interrupt test groups: 725 passed and 7 expected skips with warnings as errors.
- [x] Run the broader wizard suite and repository-required checks appropriate to the final diff; the isolated real Docker `restart n8n` smoke returned 0, streamed Restarting/Started, and left no campaign container or network.
- [x] Inspect the complete diff for task-registration races, cancellation propagation, process ownership, return-code preservation, redaction, stdout/stderr restoration, and monkey-patch restoration.
- [x] Obtain an independent issue-scoped review, present concrete findings, fix every valid finding, and rerun affected checks. The sole P2 fixture-startup race was fixed; final re-review has no unresolved findings.
- [ ] Run `git diff --check`, commit with the public no-reply identity, push, and open the issue PR to `develop` with the AC evidence. (`git diff --check` passed; delivery remains.)
- [ ] Wait for the latest required CI, resolve failures and conversations, squash-merge, and verify the merged develop revision and post-merge checks.
- [ ] Inspect and merge the direct `develop`-to-`main` promotion through current protection, then verify main contents, required post-merge CI, and docs publication if triggered.
- [ ] Comment on and close #1031, mark its project item Done, delete only this campaign's local/remote branch, prune refs, remove disposable fixture processes/files, and checkpoint the ledger before #1030.

## 8. Baseline and research evidence

- Live #1031 is open and Todo with no comments, assignee, milestone, blocker, subissue, or linked branch.
- Current develop/main trees are identical before the issue branch; the branch was created from develop `32693d4f` and pushed before edits.
- Static trace confirms `asyncio.to_thread(starter._reactivate_n8n_if_needed)` reaches the raw unbounded patch.
- Python 3.12 documents `run_coroutine_threadsafe` as the thread-safe coroutine submission API and permits cancellation through its returned future. Python cancellation guidance requires cleanup in `finally` and propagation of `CancelledError`.
- Docker documents `compose restart` as restarting the selected service containers and separately exposes a container shutdown timeout. Client cleanup cannot establish daemon-side cancellation, matching closed #1001.
- No related implementation PR or linked branch exists. Open Dependabot PR #1071 is unrelated and excluded.
