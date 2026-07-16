from __future__ import annotations

import subprocess


def test_gltf_transform_runner_includes_normalization_and_optimization_flags(
    monkeypatch, tmp_path
) -> None:
    from asset_worker.models import PostprocessParams
    from asset_worker.runner import run_gltf_transform

    commands = []

    def fake_run(command, *, check, timeout):
        commands.append(command)
        assert check is True
        assert timeout == 300.0
        if command[1] == "optimize":
            tmp_path.joinpath("out.glb").write_bytes(b"optimized")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    input_path = tmp_path / "in.glb"
    output_path = tmp_path / "out.glb"
    input_path.write_bytes(b"raw")

    run_gltf_transform(
        input_path,
        output_path,
        PostprocessParams(
            target_height_m=1.8,
            simplify_ratio=0.4,
            draco=True,
            meshopt=True,
            ktx2=True,
            collider_decimation=0.2,
        ),
    )

    rendered = [" ".join(command) for command in commands]
    assert any("inspect" in command for command in rendered)
    assert any("validate" in command for command in rendered)
    optimize = next(command for command in commands if command[1] == "optimize")
    assert "--instance" in optimize
    assert "--weld" in optimize
    assert "--simplify" in optimize
    assert "--simplify-ratio" in optimize
    assert "0.4" in optimize
    assert "--compress" in optimize
    assert "draco" in optimize
    assert "--texture-compress" in optimize
    assert "ktx2" in optimize
    assert output_path.exists()


def test_gltf_transform_runner_applies_timeout_to_every_subprocess(
    monkeypatch, tmp_path
) -> None:
    from asset_worker.models import PostprocessParams
    from asset_worker.runner import run_gltf_transform

    timeouts = []

    def fake_run(command, *, check, timeout):
        timeouts.append(timeout)
        if command[1] == "optimize":
            tmp_path.joinpath("out.glb").write_bytes(b"optimized")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("ASSET_WORKER_TIMEOUT_SECONDS", "17")
    input_path = tmp_path / "in.glb"
    input_path.write_bytes(b"raw")

    run_gltf_transform(input_path, tmp_path / "out.glb", PostprocessParams())

    assert timeouts == [17.0, 17.0, 17.0]
