# volumes/comfyui — runtime-generated ComfyUI manifests

Everything in this directory except this README and `.gitkeep` is a
**runtime output**: the bootstrapper's `ComfyUIManifestGenerator`
(`bootstrapper/utils/comfyui_manifest_generator.py`) rewrites
`selected-models.yaml`, `active-models.tsv`, and `active-custom-nodes.tsv`
on every start where `COMFYUI_SOURCE != disabled`. All three are
gitignored — never hand-edit them, and never `git add` them: a normal
start must not dirty the checkout (or a consumer's Atlas submodule).

The curated model catalog — the file you *do* edit — is
`services/comfyui/models.yaml`.

The two tracked marker files exist so the directory itself survives a
fresh clone: `comfyui`, `comfyui-init`, and the always-on `backend`
container bind-mount `volumes/comfyui/` read-only, and a missing host
directory would be auto-created by Docker (root-owned on rootful Linux
daemons). If the directory's contents were ever wiped, the markers are
restored by git and the manifests by the next start; every reader
tolerates their absence in the meantime.
