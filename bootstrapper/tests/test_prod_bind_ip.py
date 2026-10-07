import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")
def test_prod_bind_ip_localhost():
    # Render compose with HOST_BIND_IP set; every published port must be 127.0.0.1-bound.
    env = os.environ.copy()
    env["HOST_BIND_IP"] = "127.0.0.1:"
    # provide minimal required vars by sourcing the committed .env.example.
    # --profile '*' renders the profile-gated services too (chatterbox,
    # docling-gpu, parakeet-gpu, speaches), whose ports were never checked (#1388).
    out = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml", "--env-file", ".env.example",
         "--profile", "*", "config"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    for profiled in ("chatterbox:", "docling-gpu:", "parakeet-gpu:", "speaches:"):
        assert f"\n  {profiled}\n" in out.stdout, f"{profiled} not rendered"

    # Docker Compose renders published ports in expanded form:
    #   - mode: ingress
    #     host_ip: 127.0.0.1
    #     target: <container_port>
    #     published: "<host_port>"
    # In prod mode every published port must carry host_ip: 127.0.0.1.
    # The literal string "0.0.0.0" must not appear as a host_ip value.
    assert "host_ip: 0.0.0.0" not in out.stdout, (
        "A port was rendered with host_ip 0.0.0.0 — some fragment is missing the HOST_BIND_IP prefix"
    )
    # Strong guard: EVERY published port must carry host_ip: 127.0.0.1 in prod.
    # A missing prefix renders a published port with NO host_ip line, so the
    # count of host_ip:127.0.0.1 must equal the count of published ports.
    published = out.stdout.count("published:")
    localhost_bound = out.stdout.count("host_ip: 127.0.0.1")
    assert published > 0, "no published ports rendered — env-file/interpolation problem"
    assert localhost_bound == published, (
        f"{published - localhost_bound} published port(s) are NOT bound to 127.0.0.1 in prod — "
        "a fragment is missing the HOST_BIND_IP prefix"
    )
