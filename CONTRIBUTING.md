# 9.9. Contributing

This is the short path from a fresh clone to a merged first change: set up, run one safe test for the area you touch, and open a pull request against the right branch. Adding or changing a service has its own walkthrough in [Adding a service](docs/CONTRIBUTING-services.md). Report security findings through the [security policy](SECURITY.md), never as a public issue.

## 1. Before you start

- Pick or open an [issue](https://github.com/thekaveh/atlas/issues) on the project's GitHub issue tracker. For anything larger than a typo, say on the issue what you plan to change, so the scope is agreed before you write it.
- You need `git`, [uv](https://docs.astral.sh/uv/), and Python 3.10 or newer for the bootstrapper. Backend tests run under Python 3.12, which `uv` can download for you. The documentation checks in §3.3 also need `make` and the native Cairo library that diagram rendering links (`libcairo2` on Debian and Ubuntu, `brew install cairo` on macOS). Docker is only needed to run the stack itself and the Docker-backed suites described in §4.

## 2. Set up

```bash
git clone https://github.com/thekaveh/atlas.git
cd atlas
git switch develop
uv sync --project bootstrapper
```

`uv sync` installs the bootstrapper and its development group (pytest and the documentation build) into `bootstrapper/.venv`. Every command below runs from the repository root.

## 3. Run one safe test for your change

Run the test closest to what you changed before you push. Each command below runs without Docker, without a running stack, and without any live service endpoint. The first run downloads what it needs (Python 3.12 and the Backend's packages for §3.2, fonts and diagram assets for the documentation build), so it needs network access once.

| You change | Run |
|---|---|
| The bootstrapper: `start.sh`/`stop.sh` flow, wizard, manifest loader or validator, docs tooling | §3.1, choosing the test file named after the module you touched |
| A service manifest (`services/<name>/service.yml`) | §3.1 and §3.3, then the checks in [Adding a service](docs/CONTRIBUTING-services.md) |
| The Backend API (`services/backend/app/app/`) | §3.2 |
| Documentation only | §3.3 |

### 3.1. Bootstrapper

```bash
uv run --project bootstrapper pytest bootstrapper/tests/test_manifests.py -q
```

Swap in the test file for the module you changed, or narrow with `-k <name>`. The whole suite is `uv run --project bootstrapper pytest bootstrapper/tests -q`, but it takes around 40 minutes and part of it needs Docker (see §4).

### 3.2. Backend

The Backend has its own dependencies. This block creates a throwaway Python 3.12 environment with the runtime and test requirements under the same tested constraint CI uses, runs one non-live test file, and removes the environment afterwards. Paste it as one block: the parentheses keep the `cd` and the cleanup inside a subshell, and `set -e` stops at the first failing step, so a failed download reports itself instead of a missing interpreter.

```bash
(
  set -e
  cd services/backend/app/app
  BACKEND_TEST_ROOT="$(mktemp -d)"
  trap 'rm -rf "$BACKEND_TEST_ROOT"' EXIT
  BACKEND_TEST_VENV="$BACKEND_TEST_ROOT/venv"
  uv venv --python 3.12 "$BACKEND_TEST_VENV"
  VIRTUAL_ENV="$BACKEND_TEST_VENV" uv pip install \
    -r requirements.txt -r requirements-dev.txt -c requirements-test-locked.txt
  env -u ATLAS_TEST_REDIS_URL -u ATLAS_COMFYUI_LIVE_ENDPOINT \
    -u ATLAS_TEI_RERANKER_LIVE_ENDPOINT \
    "$BACKEND_TEST_VENV/bin/python" -m pytest tests/test_readiness.py -q -W error
)
```

Replace `tests/test_readiness.py` with the test file for the route or service you changed, or use `tests/` for the whole non-live suite.

### 3.3. Documentation

```bash
make docs-check
uv run --project bootstrapper python scripts/check_doc_links.py
uv run --project bootstrapper python -m bootstrapper.docs.regen --all --check
```

Documentation is published to three surfaces (this repository, the documentation site and the wiki) from the same Markdown. `make docs-check` builds all three and fails on drift. Per-service READMEs carry generated blocks: regenerate them with `regen --all` instead of editing them by hand.

## 4. What needs Docker, and what is live

- **Docker-backed suites.** The backup, restore and database-role suites drive real containers, and the Compose checks (`test_fragment_equivalence.py`, `test_source_permutations.py`) call `docker compose`. Without Docker they skip or cannot run. The required CI jobs run all of them, so you only need them locally when you change those areas.
- **Live smokes.** A few Backend tests talk to a real Redis, ComfyUI or reranker, and skip unless their `ATLAS_*` endpoint variable is set. The command in §3.2 unsets those variables, so it never reaches a live service.
- **The stack.** `./start.sh` needs Docker and is not needed to run any test above.

## 5. Branch, pull request and required checks

- **Branch from `develop`**, for example `git switch -c fix/1234-readiness-timeout origin/develop`.
- **Open the pull request against `develop`.** Never target `main`: it only receives release pull requests that promote `develop` (see [Releasing](docs/operations/releasing.md)).
- **Title it as a Conventional Commits subject**: `type: summary`, with an optional `(scope)` after the type and a `!` before the colon only for a breaking change. For example: `fix(backend): report Redis as unavailable when the readiness probe times out`, `docs(contributing): link the backend test path`, or `feat(wizard): jump to a previous decision from review`. The squash commit takes its subject from the title, and the changelog generator reads it; the accepted types are listed in [Releasing](docs/operations/releasing.md) §6.
- **Four required checks must pass**, all run by `.github/workflows/services-lint.yml`:
  - `Manifest lint + unit tests`
  - `Compose merge + byte-equivalence + source-permutation matrix`
  - `Docs drift + audit scripts`
  - `Build-validation (Dockerfile + requirements.txt installability)`

  [Adding a service](docs/CONTRIBUTING-services.md) §13.4 says what each one catches and lists a representative local subset.
- **Before merge**, the branch must be up to date with `develop` (merge `develop` into it) and every review conversation must be resolved. A maintainer squash-merges it.
- **Link the issue** in the pull request body. The pull request targets `develop` rather than the default branch, so GitHub does not close the issue on merge; a maintainer closes it once the change lands.

## 6. What reviewers look for

- One concern per pull request, with a body that says what changed, why, and which commands you ran with their result.
- A test for every behaviour change, in the existing test file for that module where one exists.
- Documentation updated wherever the changed behaviour is described.
- Generated files regenerated, not edited: `.env.example` (`uv run --project bootstrapper python -m services.env_assembler`), the per-service Dependencies & Integrations blocks (`regen`), and never `kong-dynamic.yml`, which is rebuilt at every start.
- Adding a tracked file moves the complexity ratchet: `bootstrapper/tests/test_maintenance_baseline.py` then asks for `tracked_files` to be raised in `.maintenance.json` and in its `EXPECTED_BASELINE_SNAPSHOT`, with a refresh note naming the new file.
- No secret, API key or token-shaped literal anywhere in the diff, tests included.
- Docker images, base configurations and scanner exceptions stay as they are unless the issue is about them.

## 7. Where to go next

- [Development overview](docs/development.md) for repository layout and required documentation checks.
- [Adding a service](docs/CONTRIBUTING-services.md) for manifests, Compose fragments and the full regen and lint chain.
- [Documentation map](docs/README.md) for everything else.
