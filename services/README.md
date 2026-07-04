# `services/`

Per-service manifest folders. Each subfolder is one **service family** — a
logical grouping of co-lifecycled containers. Examples: `supabase/` owns all
eight `supabase-*` containers; `n8n/` owns `n8n` + `n8n-worker` + `n8n-init`;
`open-webui/` owns `open-web-ui` + `open-webui-init`.

The migration into this layout is complete: the root `docker-compose.yml` is
a thin `include:` shell that merges every fragment under
`services/<name>/compose.yml`, and `bootstrapper/service-configs.yml` has
been retired in favour of the per-service manifests. See
`docs/CONTRIBUTING-services.md` for the full architecture rationale.

## 1. Layout

```
services/
├── README.md                    # this file
├── supabase/
│   ├── service.yml              # manifest: env vars, sources, deps, image refs
│   ├── compose.yml              # Docker Compose fragment for the family's containers
│   ├── README.md                # service family overview (recommended)
│   └── db/scripts/              # SQL init scripts (bind-mounted into supabase-db-init)
├── redis/
│   ├── service.yml
│   └── compose.yml
└── … (≈30 more service folders)
```

A virtual service (e.g. `cloud-providers/`, `tts-provider/`, `globals/`) has
only `service.yml` — no compose fragment because the service has no
containers of its own; it owns env vars and source toggles that other
services consume.

## 2. Adding or changing a service

1. Edit `services/<name>/service.yml` (the manifest) and/or
   `services/<name>/compose.yml` (the fragment).
2. The schema lives at `bootstrapper/schemas/service.schema.json`.
3. Run the schema lint locally:
   ```bash
   uv run --project bootstrapper python -m tools.validate_fragments
   ```
   Validates every manifest against `bootstrapper/schemas/service.schema.json`
   and the cross-manifest rules. Exits non-zero on any violation.
4. If you changed any env-affecting field, also run the consistency tests
   (catch orphan `.env.example` keys, manifest vars missing from
   `.env.example`, and duplicate ownership):
   ```bash
   uv run --project bootstrapper pytest bootstrapper/tests/test_env_example_consistency.py
   ```
   Note: `.env.example` is AUTO-GENERATED from the manifests' `env:`
   declarations by `bootstrapper/services/env_assembler.py`. After adding
   or changing env vars in a `service.yml`, regenerate it:
   ```bash
   uv run --project bootstrapper python -m services.env_assembler
   ```
   `tests/test_env_assembler.py` enforces byte-equivalence between the
   committed file and the assembler's output. Never edit `.env.example`
   by hand.
5. New service? Add the fragment's path to the `include:` list in the root
   `docker-compose.yml`. Service order is now derived automatically from
   `depends_on:` topology (see `bootstrapper/services/topology.py`).

## 3. Folder-name rules

- Lowercase kebab-case (matches the `name:` field in the manifest).
- Names starting with `_` are reserved (e.g. `_user/`).
- Names starting with `.` are ignored by the loader.

## 4. Per-service `README.md`

Every non-virtual service family should ship a `README.md` describing the
containers it owns, the role of any `init/`, `build/`, `provider/`,
`extras/`, or `db/` subdirectories, and any non-obvious operational gotchas.
Virtual services (no containers) don't need one — their `service.yml`
header comments cover the same ground.

## 5. Subfolder convention

When a service brings its own source code, init scripts, build context, or
config files, those live under a named subdirectory inside the service
folder. The full convention (`app/`, `build/`, `init/`, `catalog-init/`,
`pull/`, `config/`, `db/`, `provider/`, `extras/`, `workflows-stage/`) is
documented in
[`docs/CONTRIBUTING-services.md`](../docs/CONTRIBUTING-services.md#15-subdirectory-naming-convention).

## 6. `_user/` overlay slot

Downstream forks consuming this repo as a git submodule can layer extra
services under `services/_user/<name>/` without touching the upstream tree.
The folder is gitignored upstream. Full design: see
[`docs/CONTRIBUTING-services.md`](../docs/CONTRIBUTING-services.md#21-services_user-overlay-slot-downstream-submodule-consumers).
