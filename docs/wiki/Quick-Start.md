# Quick Start

## 1. Launch

Run Atlas from the repository root:

```bash
./start.sh
```

The wizard prompts for the track, SOURCE values, base port, hosts setup, and final launch confirmation.

## 2. Common Launch Variants

```bash
./start.sh --track gen-ai-rag
./start.sh --track data-eng
./start.sh --base-port 64000
./start.sh --setup-hosts
./start.sh --no-tui
```

## 3. Hosts And Gateway

Run `./start.sh --setup-hosts` when you want Kong `*.localhost` aliases. Without that step, direct localhost ports still work for services that expose them.

## 4. First Places To Open

- Atlas root dashboard: `http://localhost:63000`
- Kong-hosted service aliases: see [Reference](Reference)
- Service-specific docs: see [Services](Services)

## 5. Stop And Reset

```bash
./stop.sh
./stop.sh --cold
./stop.sh --clean-hosts
```
