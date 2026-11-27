#!/bin/bash
# Atlas (#2107): the custom-nodes volume can hold another core's bytes for the tracked
# files under custom_nodes/, and git refuses the AI-Dock update checkout while those
# files are dirty. Restore only the tracked files under custom_nodes/ to the checked-out
# commit before update-comfyui.sh runs. Untracked custom nodes are never touched.
# init.sh sources this file, so it runs in the same shell as the other preflight scripts.
if [[ -d /opt/ComfyUI/.git ]]; then
    tracked=$(git -C /opt/ComfyUI ls-files -- custom_nodes 2>/dev/null)
    if [[ -n "$tracked" ]]; then
        printf "Restoring tracked custom_nodes files to the checked-out core\n"
        git -C /opt/ComfyUI checkout -- "$tracked" 2>/dev/null || true
    fi
fi
