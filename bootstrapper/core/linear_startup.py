"""Headless Atlas launch pipeline used by the Click entrypoint."""

from __future__ import annotations

import json
import io
import os
import sys
from contextlib import contextmanager, redirect_stdout
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any

from utils.submodule_pin_guard import warn_if_submodule_pin_drifted


_JSON_CLI_PAYLOAD: ContextVar[list[str | None] | None] = ContextVar(
    "atlas_json_cli_payload", default=None
)


@dataclass(frozen=True)
class LinearStartupOptions:
    cold: bool
    base_port: int | str | None
    project_name: str | None
    source_args: dict[str, Any]
    profile: str | None
    explicit_prometheus: str | None
    explicit_grafana: str | None
    cloud_api_keys: dict[str, str]
    user_model_selections: dict[str, str]
    no_port_migrate: bool
    setup_hosts: bool
    skip_hosts: bool
    track: str | None
    detach: bool
    json_output: bool
    no_splash: bool


def run_linear_startup(starter: Any, options: LinearStartupOptions) -> int:
    """Run the non-Textual startup sequence and return a process exit code."""
    if not options.json_output:
        return _run_linear_startup(starter, options)

    outer_payload = _JSON_CLI_PAYLOAD.get()
    if outer_payload is not None:
        return _run_linear_startup(starter, options, summary_payload=outer_payload)

    json_stdout = sys.stdout
    summary_payload = [None]
    with _pipeline_stdout_to_stderr():
        code = _run_linear_startup(
            starter,
            options,
            summary_payload=summary_payload,
        )
    if summary_payload[0] is None:
        print(json.dumps({"ok": code == 0, "exit_code": code}), file=json_stdout)
    else:
        print(summary_payload[0], end="", file=json_stdout)
    return code


def json_cli_guard(func):
    """Reserve stdout for one terminal JSON document for a whole CLI run."""
    @wraps(func)
    def wrapped(*args, **kwargs):
        invoked_subcommand = bool(
            args and getattr(args[0], "invoked_subcommand", None)
        )
        if invoked_subcommand or not kwargs.get("json_output", False):
            return func(*args, **kwargs)

        json_stdout = sys.stdout
        payload: list[str | None] = [None]
        token = _JSON_CLI_PAYLOAD.set(payload)
        caught: BaseException | None = None
        result = None
        try:
            with _pipeline_stdout_to_stderr():
                try:
                    result = func(*args, **kwargs)
                except BaseException as exc:  # re-raised after terminal JSON
                    caught = exc
        finally:
            _JSON_CLI_PAYLOAD.reset(token)

        if caught is None:
            exit_code = 0
        elif isinstance(caught, SystemExit) and isinstance(caught.code, int):
            exit_code = caught.code
        else:
            exit_code = int(getattr(caught, "exit_code", 1) or 1)
        terminal = payload[0] or json.dumps(
            {"ok": exit_code == 0, "exit_code": exit_code}
        ) + "\n"
        print(terminal, end="", file=json_stdout)
        if caught is not None:
            setattr(caught, "_atlas_json_emitted", True)
            raise caught
        return result

    return wrapped


@contextmanager
def _pipeline_stdout_to_stderr():
    """Route Python and inherited child-process stdout to stderr."""
    saved_fd = None
    stdout_fd = 1
    try:
        sys.stdout.flush()
        saved_fd = os.dup(stdout_fd)
        os.dup2(2, stdout_fd)
    except OSError:
        if saved_fd is not None:
            os.close(saved_fd)
        saved_fd = None
    try:
        with redirect_stdout(sys.stderr):
            yield
    finally:
        if saved_fd is not None:
            sys.stderr.flush()
            os.dup2(saved_fd, stdout_fd)
            os.close(saved_fd)


def _run_linear_startup(
    starter: Any,
    options: LinearStartupOptions,
    *,
    summary_payload: list[str | None] | None = None,
) -> int:
    starter.no_splash = options.no_splash
    starter.profile = options.profile
    starter.show_banner()

    if not starter.prepare_environment(
        cold_start=options.cold,
        base_port=options.base_port,
        project_name=options.project_name,
    ):
        return 1
    if not starter.backfill_missing_env_vars():
        return 1
    if not starter.apply_source_overrides(**options.source_args):
        return 1
    if not starter.apply_profile_overrides(
        options.profile or "default",
        explicit_prometheus=options.explicit_prometheus,
        explicit_grafana=options.explicit_grafana,
    ):
        return 1
    if not starter.apply_cloud_api_keys(options.cloud_api_keys):
        return 1
    if not starter.apply_user_model_selections(options.user_model_selections):
        return 1

    starter.run_port_migration(options.no_port_migrate)

    if options.profile == "prod":
        service_sources = starter.config_parser.parse_service_sources()
        starter.source_validator.validation_errors = []
        if not starter.source_validator.validate_sources_for_profile(
            service_sources, "prod"
        ):
            starter.source_validator.print_validation_results()
            return 1
    if not starter.validate_source_configurations():
        return 1

    starter.unset_port_environment_variables()
    if not starter.handle_port_configuration(options.base_port):
        return 1

    # Secrets precede every derived-config writer: those writers embed the
    # database, Redis, Kong, and LiteLLM credentials they read here.
    if not starter.validate_supabase_keys(cold_start=options.cold):
        return 1
    if not starter.generate_encryption_keys(cold_start=options.cold):
        return 1

    for step in (
        starter.generate_service_configuration,
        starter.check_service_dependencies,
        starter.generate_kong_configuration,
        starter.generate_litellm_configuration,
        starter.generate_comfyui_manifest,
    ):
        if not step():
            return 1

    if not starter.handle_hosts_configuration(
        options.setup_hosts, options.skip_hosts
    ):
        return 1

    if getattr(starter, "profile", "default") == "prod":
        try:
            starter.key_generator.assert_no_placeholders_remaining()
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    if not starter.validate_localhost_services():
        return 1
    if not starter.backfill_missing_env_vars():
        return 1

    assume_yes = options.detach or options.json_output
    if not starter.show_pre_launch_summary(
        track=options.track, assume_yes=assume_yes
    ):
        starter.banner.console.print(
            "\n  [color(245)]Launch cancelled.[/color(245)]"
        )
        return 0
    if not starter.start_managed_host_processes():
        return 1
    if not starter.start_docker_services(
        cold_start=options.cold, wait=assume_yes
    ):
        return 1

    warn_if_submodule_pin_drifted(starter.config_parser.root_dir)
    starter.show_container_status_and_verify_ports()
    starter.check_comfyui_models()
    if options.detach:
        if options.json_output:
            assert summary_payload is not None
            capture = io.StringIO()
            with redirect_stdout(capture):
                ok = starter.show_detached_status_summary(json_output=True)
            summary_payload[0] = capture.getvalue()
            return 0 if ok else 1
        return 0 if starter.show_detached_status_summary(json_output=False) else 1
    return starter.show_container_logs()
