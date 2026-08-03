# Security Policy

## 1. Project Posture

Atlas is a self-hosted, single-tenant engineering platform intended
to run on a developer's local machine or a private homelab network —
applicable across generative AI, ML, and data engineering workloads.
Its default configuration has no public web surface or shared deployment.
The optional Cloudflare Tunnel service deliberately creates a public edge;
every exposed hostname must use a least-privilege Cloudflare Access policy,
an explicit Kong origin Host override, and the destination route's own
authentication and authorization controls.

This posture shapes how we triage Dependabot and CVE alerts:
vulnerabilities are scored by **(severity × tier × reachability)**, not
raw CVSS alone.

Reachability triage must include enabled SOURCE values and edge configuration.
A route that is private in the default stack is internet-reachable when an
operator maps it through Cloudflare Tunnel, even though the application
container itself still publishes no public listener.

## 2. Operational Tiers

| Tier | Manifest examples | Where it runs |
|------|-------------------|---------------|
| **A — Container-shipped** | `services/docling/provider/gpu/requirements.txt`, `services/parakeet/provider/gpu/requirements.txt`, `services/backend/app/app/requirements.txt`, `services/jupyterhub/build/requirements.txt`, the various `*/Dockerfile`s | Docker image; ships to every user that runs `start.sh` (when the corresponding service is enabled) |
| **A — Host CLI** | `bootstrapper/pyproject.toml` | Local Python venv on every contributor's host |
| **B — Host install (opt-in)** | `services/docling/provider/localhost/pyproject.toml`, `services/parakeet/provider/mlx/requirements.txt` | Only installed when user picks the localhost/mlx provider variant and runs `uv sync` / `pip install -r` themselves |

Tier-A vulnerabilities are fast-tracked. Tier-B vulnerabilities are
documented; users who pick the localhost path own the deployment risk
on their host.

## 3. Reachability Triage Examples

- `transformers.Trainer` RCE (CVE-2026-1839, medium): **unreachable**.
  We use `transformers` transitively via easyocr for inference only and
  never instantiate `Trainer`. Dismissed as `tolerable_risk` with this
  rationale captured in the active remediation report.
- `urllib3` decompression-bomb (CVE-2026-44431/44432, high): **reachable**.
  The bootstrapper makes outbound HTTPS calls (Docker registry, Hugging Face,
  Ollama catalog). Floor-bumped immediately to clear.
- `torch.load` deserialization RCE (CVE-2025-32434, critical) in the former
  `torch==2.4.1` JupyterHub image: **remediated**. JupyterHub now ships the
  coordinated PyTorch 2.13 CPU pair and matching PyTorch-Geometric wheel set.
- `torch.jit.script` memory corruption (GHSA-rrmf-rvhw-rf47): **remediated**.
  Docling GPU and JupyterHub now use Torch 2.13.0; the Jupyter PyG family moved
  to the official 2.13 CPU wheel index and `pyg_lib` 0.8 ABI3 wheel. The unused
  torchaudio and legacy scatter/sparse/cluster extensions were removed rather
  than held on vulnerable or unavailable companion releases.
- Ragas multimodal URL-processing SSRF (CVE-2026-6587): **unreachable**.
  Backend exposes only a closed enum of text metrics and never imports the
  vulnerable multimodal collection; Jupyter use is operator-authored code.
- DiskCache pickle deserialization (CVE-2025-69872): **unreachable in shipped
  Backend routes**. `diskcache==5.6.3` is an unpatched transitive dependency of
  Ragas and Instructor, but Atlas never constructs their optional disk-cache
  adapters. Exploitation also requires write access to a cache directory that
  a later process reads. Keep this exception only while those adapters remain
  unused; remove it when upstream publishes a patched release or the transitive
  dependency disappears.
- Transformers model-loading advisories below 5.3 in Parakeet GPU:
  **operator-controlled**. NeMo 2.7.x requires Transformers 4.57.x, while
  Atlas loads only the `PARAKEET_MODEL` chosen in process environment at
  startup. Transcription requests cannot supply or change a model repository.
- PyG local wheel `pyg-lib==0.8.0+pt213cpu`: **explicit audit exclusion**.
  PyPI's advisory endpoint cannot query the PEP 440 local build identifier.
  The wheel comes from PyG's official Torch 2.13 CPU index, is ABI-coupled to
  the pinned Torch family, and had no known advisory at the 2026-08-02 review.
  CI requires this exact exclusion and fails if the local package/version drifts.
- setuptools source-distribution exclusion bypass (CVE-2026-59890):
  **remediated**. Shipped compiled graphs and the Docling localhost lock now
  resolve setuptools 83.0.0 or newer.
- Local Deep Researcher runtime graph: **remediated and audit-clean**. The
  generated hash-pinned lock now enforces patched floors for Click,
  langchain-classic, LangSmith, and Soup Sieve; the refresh command and
  byte-equivalence tests prevent those floors from silently regressing.

## 4. Public Edge Requirements

Enabling `CLOUDFLARED_SOURCE=container` changes the default trust boundary.
Before publishing a hostname, configure its Cloudflare Access application,
restrict the allowed identities, and set the Origin HTTP Host Header to the
exact Atlas Kong alias documented for the service. Keep the route's Kong and
application authentication enabled. Review logs and rotate the tunnel token
if it is exposed.

## 5. Reporting a Vulnerability

This is a personal-project repository. Please open a private security
advisory via the GitHub repository's **Security** tab → **Report a
vulnerability**. Do not file public issues for security-sensitive
findings.

## 6. Remediation Reports

Historical Dependabot remediation reports were retired from the working
tree in commit `ebdc9d4` (the `docs/security/` folder used to host them).
The reports are accessible only through `git log` / `git show`:

```bash
git log --oneline -- docs/security/                   # list the reports' history
git show ebdc9d4^:docs/security/2026-05-14-dependabot-remediation-report.md
git show ebdc9d4^:docs/security/2026-05-06-dependabot-remediation-report.md
```

- 2026-05-14 report — 77 alerts triaged, 62 phantom, 15 actionable
- 2026-05-06 report — 102 alerts triaged, Phases 1.1/1.2/1.3 landed
