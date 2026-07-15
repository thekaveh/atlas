from __future__ import annotations


def _minimal_port_env(**overrides):
    env = {
        "SUPABASE_DB_PORT": "",
        "REDIS_PORT": "",
        "SUPABASE_META_PORT": "",
        "SUPABASE_STORAGE_PORT": "",
        "SUPABASE_AUTH_PORT": "",
        "SUPABASE_API_PORT": "",
        "SUPABASE_REALTIME_PORT": "",
        "SUPABASE_STUDIO_PORT": "",
        "GRAPH_DB_PORT": "",
        "WEAVIATE_PORT": "63020",
        "WEAVIATE_SOURCE": "container",
        "WEAVIATE_SCALE": "1",
        "LOCAL_DEEP_RESEARCHER_PORT": "",
        "OPEN_WEB_UI_PORT": "",
        "BACKEND_PORT": "",
        "KONG_HTTP_PORT": "",
        "KONG_HTTPS_PORT": "",
        "N8N_PORT": "",
        "SEARXNG_PORT": "",
        "JUPYTERHUB_PORT": "",
        "LITELLM_PORT": "",
        "COMFYUI_SCALE": "0",
    }
    env.update(overrides)
    return env


def test_port_verification_skips_disabled_services(monkeypatch):
    import start as start_module

    starter = start_module.AtlasStarter()
    env = _minimal_port_env(WEAVIATE_SOURCE="disabled", WEAVIATE_SCALE="0")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(starter.config_parser, "parse_env_file", lambda: env)
    monkeypatch.setattr(
        starter.docker_manager,
        "get_service_port",
        lambda service, port: calls.append((service, port)) or "",
    )

    starter.show_container_status_and_verify_ports(on_line=lambda _msg, _level: None)

    assert ("weaviate", "8080") not in calls


def test_port_verification_skips_localhost_services(monkeypatch):
    import start as start_module

    starter = start_module.AtlasStarter()
    env = _minimal_port_env(WEAVIATE_SOURCE="localhost", WEAVIATE_SCALE="1")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(starter.config_parser, "parse_env_file", lambda: env)
    monkeypatch.setattr(
        starter.docker_manager,
        "get_service_port",
        lambda service, port: calls.append((service, port)) or "",
    )

    starter.show_container_status_and_verify_ports(on_line=lambda _msg, _level: None)

    assert ("weaviate", "8080") not in calls


def test_one_shot_init_failure_fails_startup(monkeypatch):
    import start as start_module

    starter = start_module.AtlasStarter()
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {"N8N_INIT_SCALE": "1"},
    )
    monkeypatch.setattr(
        starter.docker_manager,
        "failed_one_shot_services",
        lambda services, **_kwargs: [("n8n-init", "exit 1")],
    )

    assert starter.verify_one_shot_init_containers() is False


def test_one_shot_init_skipped_when_scale_zero(monkeypatch):
    import start as start_module

    starter = start_module.AtlasStarter()
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "N8N_INIT_SCALE": "0",
            "OPEN_WEB_UI_INIT_SCALE": "0",
            "COMFYUI_INIT_SCALE": "0",
        },
    )
    def fail_if_called(_services, **_kwargs):
        raise AssertionError("disabled init container should not be inspected")

    monkeypatch.setattr(starter.docker_manager, "failed_one_shot_services", fail_if_called)

    assert starter.verify_one_shot_init_containers() is True


def test_one_shot_init_checks_enabled_post_start_init_services(monkeypatch):
    import start as start_module

    starter = start_module.AtlasStarter()
    monkeypatch.setattr(
        starter.config_parser,
        "parse_env_file",
        lambda: {
            "N8N_INIT_SCALE": "1",
            "OPEN_WEB_UI_INIT_SCALE": "1",
            "COMFYUI_INIT_SCALE": "1",
            "REDPANDA_INIT_SCALE": "1",
            "ZEPPELIN_INIT_SCALE": "1",
        },
    )
    monkeypatch.setattr(
        starter.config_parser,
        "load_consumer_config",
        lambda: type("ConsumerConfig", (), {"n8n_workflows": [object()]})(),
    )
    calls = []

    def fake_failed_one_shot_services(services, **kwargs):
        calls.append((services, kwargs))
        return []

    monkeypatch.setattr(
        starter.docker_manager,
        "failed_one_shot_services",
        fake_failed_one_shot_services,
    )

    assert starter.verify_one_shot_init_containers() is True
    assert calls == [
        (
            [
                "n8n-init",
                "open-webui-init",
                "comfyui-init",
                "redpanda-init",
                "zeppelin-init",
                "n8n-seed",
            ],
            {"timeout_seconds": 900.0, "poll_interval_seconds": 5.0},
        )
    ]


def test_one_shot_waits_for_terminal_failure(monkeypatch):
    from core.docker_manager import DockerManager

    dm = DockerManager()
    states = iter([
        ([{"State": "running", "Status": "Up 1 second", "ExitCode": ""}], None),
        ([{"State": "exited", "Status": "Exited (1)", "ExitCode": "1"}], None),
    ])
    monkeypatch.setattr(dm, "_compose_ps_json", lambda _service: next(states))

    failures = dm.failed_one_shot_services(
        ["n8n-init"],
        timeout_seconds=5,
        poll_interval_seconds=0,
    )

    assert failures == [("n8n-init", "exit 1: Exited (1)")]


def test_one_shot_times_out_when_not_terminal(monkeypatch):
    from core.docker_manager import DockerManager

    dm = DockerManager()
    monkeypatch.setattr(
        dm,
        "_compose_ps_json",
        lambda _service: ([{"State": "running", "Status": "Up", "ExitCode": ""}], None),
    )

    failures = dm.failed_one_shot_services(
        ["n8n-init"],
        timeout_seconds=0,
        poll_interval_seconds=0,
    )

    assert failures == [("n8n-init", "timed out waiting for terminal state (Up)")]
