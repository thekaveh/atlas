"""#2107: the derived ComfyUI image restores tracked custom_nodes files before the update.

Without the hook, a volume holding another core's bytes for those files makes the
AI-Dock checkout fail. The hook restores only tracked custom_nodes paths and never
untracked custom nodes. This test pins the wiring and the restore command.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "services" / "comfyui" / "build"


def test_dockerfile_installs_the_preflight_hook_before_the_update_runs():
    dockerfile = (BUILD / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY atlas-preflight-custom-nodes.sh /opt/ai-dock/bin/preflight.d/05-atlas-custom-nodes.sh" in dockerfile


def test_hook_restores_only_tracked_custom_nodes_paths():
    hook = (BUILD / "atlas-preflight-custom-nodes.sh").read_text(encoding="utf-8")
    assert "git -C /opt/ComfyUI ls-files -- custom_nodes" in hook
    assert 'git -C /opt/ComfyUI checkout -- "$tracked"' in hook
    assert "rm -rf" not in hook
    assert "clean" not in hook
