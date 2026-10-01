"""What a launch reached, worded once for both front ends (#1032).

The Textual launch screen and the ``--no-tui`` linear flow still run their
own pipelines; sharing one launch plan and executor is #1043. What they
share here is the vocabulary and the final result, so for the same probe
outcomes both front ends print the same result block. Pure: no Textual, no
VMx and no I/O, so ``core/linear_startup.py`` can import it.

Stages, in launch order
-----------------------
``Configuration saved``       .env and the generated configuration are written.
``Compose converged``         ``docker compose up`` returned and the required
                              one-shot init containers succeeded.
``Service health``            healthchecks were awaited and every service is
                              running or healthy (``--detach`` only; otherwise
                              labelled skipped with the reason).
``Application verification``  the post-start probes: port mapping and the
                              ComfyUI host models directory.

Severity policy
---------------
Readiness gates decide the exit code, and are unchanged: every setup step,
``docker compose up``, the required one-shot init containers, the n8n
reactivation and, under ``--detach``, the detached health summary. A launch
that fails one stops there with its own error and a nonzero exit.

Post-start probes are advisory. They never block a launch or change its exit
code, because an optional check must not turn a running stack into a failed
start. They always qualify the result, though: ``degraded`` when a probe
found a problem, ``unverified`` when a probe raised, and a skipped probe is
named with its reason. Only ``verified`` is an unqualified success.

Lifecycle actions
-----------------
Detach, cancel startup, stop and cold stop each state what happens to the
running services, the configuration and the persistent data, so cancelling
can never be read as a teardown and only cold stop deletes anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, NamedTuple

# Probe outcomes.
VERIFIED = "verified"
FAILED = "failed"
UNVERIFIED = "unverified"
SKIPPED = "skipped"

# Final launch results. ``failed`` is reachable only through a readiness
# gate (detached health); a probe alone can at worst degrade a launch.
LAUNCH_VERIFIED = "verified"
LAUNCH_DEGRADED = "degraded"
LAUNCH_UNVERIFIED = "unverified"
LAUNCH_FAILED = "failed"

STAGE_CONFIGURATION = "Configuration saved"
STAGE_COMPOSE = "Compose converged"
STAGE_HEALTH = "Service health"
STAGE_VERIFICATION = "Application verification"

UNAVAILABLE = "not available in this configuration"

_MARKS = {VERIFIED: "✓", FAILED: "✗", UNVERIFIED: "?", SKIPPED: "–"}
_TONES = {VERIFIED: "ok", FAILED: "error", UNVERIFIED: "warn", SKIPPED: "dim"}
_HEADLINE_TONES = {
    LAUNCH_VERIFIED: "ok",
    LAUNCH_DEGRADED: "warn",
    LAUNCH_UNVERIFIED: "warn",
    LAUNCH_FAILED: "error",
}


class ProbeOutcome(NamedTuple):
    """``(name, outcome, detail)``; also used for the health stage."""

    name: str
    outcome: str
    detail: str = ""


@dataclass(frozen=True)
class ProbeSkipped:
    """Returned by a probe that does not apply to this configuration."""

    reason: str


HEALTH_NOT_AWAITED = ProbeOutcome(
    STAGE_HEALTH,
    SKIPPED,
    "not awaited; containers were started without waiting for "
    "healthchecks, and `docker compose ps` shows live health",
)


def detached_health(ok: bool) -> ProbeOutcome:
    """The health stage as the ``--detach`` status summary decided it."""
    if ok:
        return ProbeOutcome(STAGE_HEALTH, VERIFIED, "every service running or healthy")
    return ProbeOutcome(
        STAGE_HEALTH, FAILED, "not every service is running or healthy"
    )


def classify_probe_result(name: str, result: Any) -> ProbeOutcome:
    """Map what a probe returned onto the outcome vocabulary.

    ``ProbeSkipped`` is a skip with its reason; ``False`` or a positive
    count of failed checks is a failure; anything else (``None``, ``True``,
    ``0``) means the probe ran and found nothing wrong.
    """
    if isinstance(result, ProbeSkipped):
        return ProbeOutcome(name, SKIPPED, result.reason)
    if result is False:
        return ProbeOutcome(name, FAILED, "the check reported a problem")
    if type(result) is int and result > 0:
        noun = "check" if result == 1 else "checks"
        return ProbeOutcome(name, FAILED, f"{result} {noun} did not pass")
    return ProbeOutcome(name, VERIFIED)


def run_probe(name: str, probe: Callable[..., Any] | None, **kwargs: Any) -> ProbeOutcome:
    """Run one probe; an exception is reported as ``unverified``, never lost."""
    if probe is None:
        return ProbeOutcome(name, SKIPPED, UNAVAILABLE)
    try:
        result = probe(**kwargs)
    except Exception as exc:  # noqa: BLE001 - reported as the outcome
        return ProbeOutcome(name, UNVERIFIED, f"{type(exc).__name__}: {exc}")
    return classify_probe_result(name, result)


def _as_outcomes(items: Iterable[tuple[str, str, str]]) -> list[ProbeOutcome]:
    """Accept plain ``(name, outcome, detail)`` tuples as well."""
    return [ProbeOutcome(*item) for item in items]


def _with_detail(outcome: ProbeOutcome) -> str:
    return f"{outcome.name} ({outcome.detail})" if outcome.detail else outcome.name


def probe_log_lines(outcomes: Iterable[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """One ``[verify/<name>] <outcome>`` line per probe, with its log level."""
    lines = []
    for item in _as_outcomes(outcomes):
        suffix = f" — {item.detail}" if item.detail else ""
        level = "error" if item.outcome in (FAILED, UNVERIFIED) else "info"
        lines.append((f"[verify/{item.name}] {item.outcome}{suffix}", level))
    return lines


@dataclass(frozen=True)
class LaunchResult:
    """The final result block; ``rows`` are ``(text, tone)`` pairs."""

    outcome: str
    rows: tuple[tuple[str, str], ...]

    @property
    def lines(self) -> list[str]:
        return [text for text, _tone in self.rows]


def _named(outcomes: list[ProbeOutcome], wanted: str) -> list[ProbeOutcome]:
    return [item for item in outcomes if item.outcome == wanted]


def _headline(outcome: str, probes: list[ProbeOutcome], health: ProbeOutcome) -> str:
    failed = ", ".join(_with_detail(p) for p in _named(probes, FAILED))
    unverified = ", ".join(p.name for p in _named(probes, UNVERIFIED))
    skipped = ", ".join(p.name for p in _named(probes, SKIPPED))
    if outcome == LAUNCH_FAILED:
        return f"❌ Launch result: failed — service health: {health.detail}"
    problems = [f"verification failed: {failed}"] if failed else []
    problems += [f"not verified: {unverified}"] if unverified else []
    if problems:
        return (
            f"⚠️  Launch result: {outcome} — started, but "
            + "; ".join(problems) + " · containers are up"
        )
    if skipped:
        return (
            "✅ Launch result: verified — every applicable post-start check "
            f"passed (skipped: {skipped})"
        )
    return "✅ Launch result: verified — post-start verification passed"


def _verification_stage(probes: list[ProbeOutcome]) -> tuple[str, str]:
    for worst in (FAILED, UNVERIFIED, VERIFIED):
        if _named(probes, worst):
            break
    else:
        worst = SKIPPED
    detail = " · ".join(
        f"{p.name} {p.outcome}" + (f" ({p.detail})" if p.detail else "")
        for p in probes
    ) or "no probes ran"
    return f"  {_MARKS[worst]} {STAGE_VERIFICATION} — {detail}", _TONES[worst]


def _verdict(probes: list[ProbeOutcome], health: ProbeOutcome) -> str:
    if health.outcome == FAILED:
        return LAUNCH_FAILED
    if _named(probes, FAILED):
        return LAUNCH_DEGRADED
    if _named(probes, UNVERIFIED):
        return LAUNCH_UNVERIFIED
    return LAUNCH_VERIFIED


def summarize_launch(
    probes: Iterable[tuple[str, str, str]],
    *,
    health: ProbeOutcome = HEALTH_NOT_AWAITED,
    where: str = "the output above",
) -> LaunchResult:
    """State the result of a launch that got past ``docker compose up``.

    ``where`` names the place the front end shows the probe output (the
    Logs tab, or the output above), and appears only in the next action.
    Everything else is identical for both front ends.
    """
    probes = _as_outcomes(probes)
    outcome = _verdict(probes, health)
    health_detail = f"{health.outcome}: {health.detail}" if health.detail else health.outcome
    rows = [
        (_headline(outcome, probes, health), _HEADLINE_TONES[outcome]),
        (f"  ✓ {STAGE_CONFIGURATION} — .env and generated configuration written", "ok"),
        (f"  ✓ {STAGE_COMPOSE} — containers started; required init containers succeeded", "ok"),
        (f"  {_MARKS[health.outcome]} {STAGE_HEALTH} — {health_detail}", _TONES[health.outcome]),
        _verification_stage(probes),
    ]
    if outcome != LAUNCH_VERIFIED:
        rows.append((
            f"  Next: check {where} for the reason before relying on these "
            "services; `docker compose ps` shows what is running.",
            "info",
        ))
    return LaunchResult(outcome=outcome, rows=tuple(rows))


# ─── Lifecycle actions ──────────────────────────────────────────────


@dataclass(frozen=True)
class LifecycleAction:
    """One way to leave or end a launch, with all three consequences named."""

    label: str
    services: str
    configuration: str
    data: str
    destructive: bool = False

    @property
    def consequences(self) -> str:
        return f"{self.services} · {self.configuration} · {self.data}"

    def line(self, key: str, confirmation: str = "") -> str:
        note = f"; {confirmation}" if confirmation else ""
        return f"   {key} — {self.label}: {self.consequences}{note}"


DETACH = LifecycleAction(
    "Detach", "services keep running", "configuration kept", "no data deleted",
)
CANCEL = LifecycleAction(
    "Cancel startup",
    "containers already started keep running",
    "configuration written so far kept",
    "no data deleted",
)
STOP = LifecycleAction(
    "Stop", "containers stop", "configuration kept", "no data deleted (volumes kept)",
)
COLD_STOP = LifecycleAction(
    "Cold stop",
    "containers stop",
    "configuration kept",
    "persistent data DELETED (Compose-managed volumes)",
    destructive=True,
)
LIFECYCLE_ACTIONS = (DETACH, CANCEL, STOP, COLD_STOP)


def cancel_notice() -> str:
    """What a cancelled startup leaves behind, for the line printed after it."""
    return (
        f"{CANCEL.label}: {CANCEL.consequences}. "
        "./stop.sh stops them and keeps data."
    )


def launch_cancelled_notice() -> str:
    """The pre-launch summary was declined, so nothing was started."""
    return (
        f"Launch cancelled — nothing was started · {CANCEL.configuration} · "
        f"{CANCEL.data}."
    )
