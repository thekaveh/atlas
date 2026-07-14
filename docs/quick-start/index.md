# 2.1. Quick Start

## 1. Launch Atlas

Run `./start.sh` from the repository root. The setup wizard walks through track selection, service SOURCE choices, base-port selection, host aliases, and the launch summary.

## 2. Common Paths

```bash
./start.sh
./start.sh --track gen-ai-rag
./start.sh --track data-eng
./start.sh --base-port 64000
./start.sh --setup-hosts
```

## 3. First Services To Visit

Use the Atlas root dashboard at `http://localhost:63000` after launch. Direct service URLs and Kong aliases are listed in the generated service catalog and ports reference.
