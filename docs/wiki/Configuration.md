# Configuration

## 1. Environment

`.env.example` is generated from manifests and topology defaults. `.env` stores local runtime choices and generated secrets.

## 2. SOURCE Flags

Wizard selections can also be passed as `./start.sh --<service>-source <value>` flags. Use [Reference](Reference) for the generated SOURCE matrix.

## 3. Ports

All ports derive from `BASE_PORT`, whose default is `63000`. Change it with:

```bash
./start.sh --base-port 64000
```

## 4. Hosts

Use `./start.sh --setup-hosts` for Kong `*.localhost` aliases. Use `./stop.sh --clean-hosts` to remove Atlas-managed host entries.

## 5. Generated Surface Count

- SOURCE surfaces: `61`
- Environment variables: `703`
- Services with ports or aliases: `47`

## 6. Safe Editing Rules

- Change manifests before changing generated references.
- Regenerate `.env.example` when env declarations or port slots change.
- Regenerate docs when SOURCE values, tracks, aliases, or dependencies change.
- Keep local secrets in `.env`, not in tracked docs or manifests.

## 7. Troubleshooting Configuration

- Use a different `BASE_PORT` when ports collide.
- Re-run hosts setup after changing aliases.
- Prefer SOURCE flags for repeatable launch scripts.
- Check generated reference tables before assuming a service exposes a port.
