# Bounded TUI Compose reactivation (#1031)

> **Status: archived spec** — dated 2026-09-14; a point-in-time record kept for archaeology, not current guidance. The landed outcome is recorded in the [CHANGELOG](../../CHANGELOG.md).

## 1. Verified problem

At current `develop` commit `32693d4f`, the Textual launch pipeline temporarily
replaces `DockerManager.execute_compose_command` with a closure that launches a
raw `subprocess.Popen`, reads its merged output, and waits without a deadline or
process-group ownership. `AtlasStarter._reactivate_n8n_if_needed` invokes that
closure through `asyncio.to_thread` after Compose starts the stack. Cancelling
the Textual worker cancels the await, but Python cannot stop the worker thread;
the raw Compose client and any descendants can therefore remain alive.

The same screen already has `_run_streamed_command`, an async runner that emits
each line, applies the existing 30-minute finite-Compose deadline, starts a new
POSIX session, sends TERM followed by KILL after a two-second grace period, reaps
the leader, and propagates caller cancellation after cleanup. The missing piece
is a safe bridge from the synchronous `execute_compose_command` seam in the
worker thread to that event-loop-owned runner.

## 2. Design

Replace the closure with a small pipeline-scoped executor. Its synchronous call
builds the same Compose argv, keeps `--ansi=never`, sets plain BuildKit progress,
and submits `_run_streamed_command` to the Textual event loop with
`asyncio.run_coroutine_threadsafe`. It blocks the worker thread on the returned
future so `AtlasStarter` retains its existing integer return-code contract.

The executor tracks the asyncio tasks it creates and has one cancellation latch.
Pipeline helpers run through an awaitable method that shields the `to_thread`
task. If Textual cancels the pipeline, that method sets the latch, cancels only
the executor's registered tasks, awaits their process-tree cleanup, waits for the
worker thread to settle, then re-raises `CancelledError`. A task submitted during
the cancellation race observes the latch before launching a process. The latch
belongs to one launch pipeline and is never shared with unrelated commands or
processes.

Successful output uses the existing classifier and log sink, preserving the
current command echo, progress lines, colors, and n8n health convergence.
Timeout returns 124 after the runner logs its deadline. Cancellation returns 130
to the synchronous helper while the outer coroutine preserves cancellation.
Other nonzero returns add a stable diagnostic containing only the numeric exit
status; launch failures name the exception type without interpolating captured
output or argv. Streamed output remains available in the launch log for normal
diagnosis.

The two-second cleanup grace bounds TERM-to-KILL teardown for an active Compose
client process. Once that client exits successfully, n8n's existing synchronous
health-convergence polling retains its separate 120-second window; the process
cleanup grace does not describe or shorten that post-restart health check.

## 3. Alternatives considered

Extending the global synchronous `core.process_runner.run_with_deadline` with a
second streaming and cancellation subsystem would duplicate the screen's mature
async runner and broaden a TUI-only fix. Reimplementing n8n reactivation as a
special async path would leave the unsafe monkey patch in place for any other
pipeline helper. Calling `Future.cancel()` without awaiting the event-loop task
would let the worker settle before process cleanup finishes. The selected bridge
reuses the current owner and gives the pipeline one explicit cleanup boundary.

## 4. Scope and verification

Controlled subprocess tests exercise the executor that is installed as the
actual TUI monkey patch: silence until timeout, endless output, a spawned child,
nonzero exit, successful streamed output, and cancellation. Tests record PIDs
and verify descendants stop while an unrelated process remains alive. A focused
pipeline test confirms failed reactivation marks launch failure and cancellation
is re-raised only after cleanup. Existing async-runner and n8n suites provide
regression coverage.

No headless `DockerManager` contract, container timeout, image, service manifest,
or daemon operation changes. Stopping the Compose client cannot prove that the
Docker daemon cancelled work it already accepted; this remains the documented
boundary from closed #1001. A Docker smoke may prove the ordinary `restart n8n`
path when a disposable Atlas stack is available, but controlled process fixtures
provide the required ownership and deadline evidence without touching user
containers.

## 5. Delivery and rollback

Deliver through the issue branch into `develop`, then the protected
`develop`-to-`main` promotion. Reverting the issue commit restores the former
TUI executor; there is no migration or persistent-data change. After post-merge
verification, close #1031, mark its board item Done, and delete only this
campaign's branch and disposable fixtures.
