"""Headless Atlas launch pipeline used by the Click entrypoint."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

from utils.submodule_pin_guard import warn_if_submodule_pin_drifted


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
        return 0 if starter.show_detached_status_summary(
            json_output=options.json_output
        ) else 1
    starter.show_container_logs()
    return 0
