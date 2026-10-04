# 9.8. Security Policy

## 1. Reporting a Vulnerability

This is a personal-project repository. Please open a private security
advisory via the GitHub repository's **Security** tab → **Report a
vulnerability**. Do not file public issues for security-sensitive
findings.

Include in the advisory:

- **Revision**: the commit SHA you ran (`git rev-parse HEAD`) and, if you
  pinned one, the release tag. §2 says which revisions receive fixes.
- **Enabled SOURCE values**: the `*_SOURCE` settings from your `.env` for
  every service involved, with keys, tokens, and passwords removed.
- **Exposure boundary**: where the affected service is reachable from — the
  local machine only, a private LAN or homelab network, or a public hostname
  published through Cloudflare Tunnel, together with the Cloudflare Access
  policy in front of it (§6).
- **Minimal reproduction**: the smallest steps, request, or proof of concept
  that shows the issue, shared only inside the private advisory.

Triage uses these to score reachability under the posture and tier rules in
§3 and §4. Responses and fixes are best-effort; there is no committed
acknowledgement or fix timeline.

## 2. Supported Revisions

Security fixes target the current tips of `develop` and `main`. A fix lands
on `develop` by pull request and reaches `main` through the Gitflow
promotion; `main` is the rolling tip described in
[Releasing & version tags](docs/operations/releasing.md).

Release tags (`vMAJOR.MINOR.PATCH`, cut on `main`) are pinnable checkpoints,
not supported release lines. A pushed tag is immutable, and a released
checkpoint is corrected only by a new tag, so historical tags, including
`v0.1.0`, and older commits are not supported and receive no backported
fixes. A consumer pinned to a tag picks up a fix by moving the pin to a
later tag that contains it or, when no such tag exists yet, to a `main`
commit that does.

## 3. Project Posture

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

## 4. Operational Tiers

| Tier | Manifest examples | Where it runs |
|------|-------------------|---------------|
| **A — Container-shipped** | `services/docling/provider/gpu/requirements.txt`, `services/parakeet/provider/gpu/requirements.txt`, `services/backend/app/app/requirements.txt`, `services/jupyterhub/build/requirements.txt`, the various `*/Dockerfile`s | Docker image; ships to every user that runs `start.sh` (when the corresponding service is enabled) |
| **A — Host CLI** | `bootstrapper/pyproject.toml` | Local Python venv on every contributor's host |
| **B — Host install (opt-in)** | `services/docling/provider/localhost/pyproject.toml`, `services/parakeet/provider/mlx/requirements.txt` | Only installed when user picks the localhost/mlx provider variant and runs `uv sync` / `pip install -r` themselves |

Tier-A vulnerabilities are fast-tracked. Tier-B vulnerabilities are
documented; users who pick the localhost path own the deployment risk
on their host.

## 5. Reachability Triage Examples

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
  Docling GPU and JupyterHub now use Torch 2.13.0; the Jupyter PyG family
  resolves torch-geometric from PyPI and carries no compiled accelerator. The
  unused torchaudio, the legacy scatter/sparse/cluster extensions, and
  `pyg_lib` were removed rather than held on vulnerable, unavailable, or
  single-CDN companion releases.
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
  CI requires this exact exclusion and fails if the local package/version drifts.
- setuptools source-distribution exclusion bypass (CVE-2026-59890):
  **remediated**. Shipped compiled graphs and the Docling localhost lock now
  resolve setuptools 83.0.0 or newer.
- Local Deep Researcher runtime graph: **remediated and audit-clean**. The
  generated hash-pinned lock now enforces patched floors for Click,
  langchain-classic, LangSmith, and Soup Sieve; the refresh command and
  byte-equivalence tests prevent those floors from silently regressing.

## 6. Public Edge Requirements

Enabling `CLOUDFLARED_SOURCE=container` changes the default trust boundary.
Before publishing a hostname, configure its Cloudflare Access application,
restrict the allowed identities, and set the Origin HTTP Host Header to the
exact Atlas Kong alias documented for the service. Keep the route's Kong and
application authentication enabled. Review logs and rotate the tunnel token
if it is exposed.

## 7. Automated Scanning

Two container-image gates run in `services-lint.yml` on every pull request:

- **`Build-validation (Dockerfile + requirements.txt installability)`** is a
  required check. It builds every local Dockerfile context, verifies the two
  commit-pinned remote build contexts against their reviewed base-image index
  digests, and Trivy-scans the manifest-owned remote images whose declarations
  changed in the pull request.
- **`Final-image scan (local Compose and init images)`** builds every local
  Compose and init image and fails on any HIGH or CRITICAL finding. A finding
  does not stop the run: every image is scanned, and the job fails at the end
  with the list of images that did not pass. A Trivy error still stops it at
  once. It reports on every run but is **not yet a required check** while the
  fleet still carries findings, and its promotion to the required set is
  tracked in the issue linked from the CI workflow (#1002).

Findings that cannot be fixed at the pinned version are excepted in
`.trivyignore.yaml`. Every exception is scoped to an exact package version or
path, carries a review statement, and expires; the scan refuses to run on a
broad, duplicate, or stale entry, and a test fails any statement that names no
owning service. A package-version scope matches that version in every scanned
image, not only the one its statement names; reconciling each row against
per-image scan output is tracked in #1289. Reachability triage for those
exceptions follows the same tier and edge rules as §3 and §4.

## 8. Remediation Reports

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
