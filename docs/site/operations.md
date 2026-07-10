# Operations

## 1. Runtime Commands

```bash
./start.sh
./start.sh env backfill
./start.sh compose validate
./start.sh --no-tui --detach
./start.sh --no-tui --detach --json
./stop.sh
./stop.sh --cold
./stop.sh --clean-hosts
```

## 2. Automation

Use `./start.sh --no-tui --detach` for scripted bring-up. The alias
`--no-follow` is equivalent. Atlas runs the normal start pipeline, waits for
Compose health gates, prints a per-service status summary, and exits instead of
following logs. Add `--json` for machine-readable status in CI or parent-repo
wrappers.

## 3. Headless Validation

Use `./start.sh env backfill` after updating an Atlas submodule pin. It
preserves existing values, appends newly introduced `.env.example` keys, fills
blank values only when the new example carries a non-blank default, and reports
the affected keys by source section. Then run `./start.sh compose validate` to
validate the assembled stack, including `services/_user/<name>/compose.yml`
overlays. Exit code `0` means the env backfill or Compose validation succeeded;
`compose validate` returns Compose's failing status code when validation fails.

## 4. Health And Logs

The launch phase streams Docker Compose output through the Textual UI. The same command path works without the TUI in non-interactive environments.
