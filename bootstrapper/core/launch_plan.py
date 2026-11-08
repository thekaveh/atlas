"""The ordered configuration stages both launch front ends run (#1043).

``core/linear_startup.py`` (``--no-tui``) and the Textual launch screen each
kept their own copy of this sequence, so adding or reordering a stage took
two edits that could drift apart. Both now iterate ``CONFIGURE_STAGES`` and
run each entry through ``run_stage``. A new stage needs one registration
here.

The plan covers the stages from the source overrides to the final ``.env``
backfill. What runs before it (environment preparation, port migration) and
after it (Compose up, init checks, post-start probes) is still owned by each
front end.

No Textual, no I/O of its own: every stage calls the shared starter. A stage
that fails with an explanation raises ``StageFailure``; the front end shows
the result's ``detail``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

SUCCEEDED = "succeeded"
FAILED = "failed"
CRASHED = "crashed"
CANCELLED = "cancelled"


@dataclass(frozen=True)
class LaunchInputs:
    """What one launch asked for, resolved the same way by both front ends."""

    source_args: Mapping[str, Any] = field(default_factory=dict)
    profile: str = "default"
    cloud_api_keys: Mapping[str, str] = field(default_factory=dict)
    user_model_selections: Mapping[str, str] = field(default_factory=dict)
    cold: bool = False
    base_port: int | str | None = None
    setup_hosts: bool = False
    skip_hosts: bool = False


class StageFailure(Exception):
    """A stage refused to continue, for the reason in its message."""


@dataclass(frozen=True)
class LaunchStage:
    id: str
    label: str
    run: Callable[[Any, LaunchInputs], bool]


@dataclass(frozen=True)
class StageResult:
    stage_id: str
    label: str
    status: str
    detail: str = ""
    # The exception a crashed stage raised, so a front end can re-raise it.
    error: Exception | None = field(default=None, compare=False, repr=False)

    @property
    def ok(self) -> bool:
        return self.status == SUCCEEDED


def _validate_sources_for_profile(starter: Any, inputs: LaunchInputs) -> bool:
    """Refuse a dev-only source (for example ollama-localhost) under prod."""
    if inputs.profile != "prod":
        return True
    service_sources = starter.config_parser.parse_service_sources()
    starter.source_validator.validation_errors = []
    if starter.source_validator.validate_sources_for_profile(service_sources, "prod"):
        return True
    starter.source_validator.print_validation_results()
    return False


def _assert_no_placeholder_secrets(starter: Any, inputs: LaunchInputs) -> bool:
    """Refuse a prod launch while a managed secret is still its placeholder.

    Runs after key generation, so a first-run rotation passes cleanly."""
    if inputs.profile != "prod":
        return True
    try:
        starter.key_generator.assert_no_placeholders_remaining()
    except RuntimeError as exc:
        raise StageFailure(str(exc)) from exc
    return True


def _call(method: str) -> Callable[[Any, LaunchInputs], bool]:
    return lambda starter, _inputs: getattr(starter, method)()


# Order matters:
# - Profile overrides follow source overrides, so this run's explicit
#   sources win over the profile's.
# - Managed-host preflight precedes port configuration, which stops a
#   running stack on a warm start (#1342).
# - Secrets precede every derived-config writer, which embeds the database,
#   Redis, Kong and LiteLLM credentials it reads.
# - The final backfill catches entries added to .env.example after .env was
#   last written.
CONFIGURE_STAGES: tuple[LaunchStage, ...] = (
    LaunchStage("apply-source-overrides", "Apply source overrides",
                lambda s, i: s.apply_source_overrides(**i.source_args)),
    LaunchStage("apply-profile-overrides", "Apply profile overrides",
                lambda s, i: s.apply_profile_overrides(i.profile)),
    LaunchStage("validate-sources-for-profile", "Validate sources for profile",
                _validate_sources_for_profile),
    LaunchStage("apply-cloud-api-keys", "Apply cloud API keys",
                lambda s, i: s.apply_cloud_api_keys(dict(i.cloud_api_keys))),
    LaunchStage("apply-user-model-selections", "Apply user model selections",
                lambda s, i: s.apply_user_model_selections(dict(i.user_model_selections))),
    LaunchStage("validate-source-configurations", "Validate source configurations",
                _call("validate_source_configurations")),
    LaunchStage("reconcile-default-models", "Reconcile default models",
                _call("reconcile_default_models")),
    LaunchStage("preflight-managed-hosts", "Preflight managed hosts",
                _call("preflight_managed_host_processes")),
    LaunchStage("clear-port-environment", "Clear stale port environment",
                lambda s, _i: s.unset_port_environment_variables() or True),
    LaunchStage("configure-ports", "Configure ports",
                lambda s, i: s.handle_port_configuration(i.base_port)),
    LaunchStage("validate-supabase-keys", "Validate Supabase keys",
                lambda s, i: s.validate_supabase_keys(cold_start=i.cold)),
    LaunchStage("generate-encryption-keys", "Generate encryption keys",
                lambda s, i: s.generate_encryption_keys(cold_start=i.cold)),
    LaunchStage("generate-service-configuration", "Generate service configuration",
                _call("generate_service_configuration")),
    LaunchStage("check-service-dependencies", "Check service dependencies",
                _call("check_service_dependencies")),
    LaunchStage("generate-kong-configuration", "Generate Kong configuration",
                _call("generate_kong_configuration")),
    LaunchStage("generate-litellm-configuration", "Generate LiteLLM configuration",
                _call("generate_litellm_configuration")),
    LaunchStage("generate-comfyui-manifest", "Generate ComfyUI manifest",
                _call("generate_comfyui_manifest")),
    LaunchStage("configure-hosts", "Configure hosts",
                lambda s, i: s.handle_hosts_configuration(i.setup_hosts, i.skip_hosts)),
    LaunchStage("assert-no-placeholder-secrets", "Assert no placeholder secrets",
                _assert_no_placeholder_secrets),
    LaunchStage("validate-localhost-services", "Validate localhost services",
                _call("validate_localhost_services")),
    LaunchStage("backfill-env", "Backfill .env from .env.example",
                _call("backfill_missing_env_vars")),
)


def run_stage(
    stage: LaunchStage,
    starter: Any,
    inputs: LaunchInputs,
    *,
    should_stop: Callable[[], bool] = lambda: False,
) -> StageResult:
    """Run one stage and say how it ended, without raising."""
    if should_stop():
        return StageResult(stage.id, stage.label, CANCELLED, "cancelled before it started")
    try:
        ok = stage.run(starter, inputs)
    except StageFailure as exc:
        return StageResult(stage.id, stage.label, FAILED, str(exc))
    except Exception as exc:  # noqa: BLE001 - a crash is a result, not an escape
        return StageResult(stage.id, stage.label, CRASHED, str(exc), exc)
    return StageResult(stage.id, stage.label, SUCCEEDED if ok else FAILED)
