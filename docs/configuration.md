# Configuration

## 1. Environment Files

`.env.example` is generated from service manifests and topology defaults. `.env` stores the local runtime choices.

## 2. SOURCE Overrides

Every SOURCE value can be selected through the wizard or passed as a CLI flag such as `--weaviate-source localhost`.

## 3. Ports

Ports are derived from `BASE_PORT` and service-specific slots. Change the base with `./start.sh --base-port 64000`.
