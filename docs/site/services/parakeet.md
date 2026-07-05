# Parakeet (NVIDIA STT engine)

## 1. Overview

`parakeet` is an Atlas service family in the `media` category. Its implementation and service-owned documentation live under `services/parakeet/`.

## 2. Role In Atlas

Atlas uses this service according to its manifest, topology row, SOURCE settings, dependencies, and runtime data-flow declarations.

## 3. Tracks And Category

- Category: `media`
- Kind: `container`
- Tracks: `all`

## 4. Access

- Kong aliases: `stt.localhost`
- Port variables: `STT_PROVIDER_PORT, PARAKEET_LOCALHOST_PORT, WHISPER_CPP_LOCALHOST_PORT`

## 5. Configuration

- SOURCE variable: `STT_PROVIDER_SOURCE`
- Default SOURCE: `speaches-container-cpu`
- Available SOURCE values: `speaches-container-cpu, speaches-container-gpu, parakeet-container-gpu, parakeet-localhost, whisper-cpp-localhost, disabled`

## 6. Dependencies And Topology

- Required dependencies: `litellm`
- Optional dependencies: `-`
- Runtime calls: `-`

## 7. Source Values

| SOURCE Variable | Default | Values |
| --- | --- | --- |
| STT_PROVIDER_SOURCE | speaches-container-cpu | speaches-container-cpu, speaches-container-gpu, parakeet-container-gpu, parakeet-localhost, whisper-cpp-localhost, disabled |

## 8. Runtime Integration

The manifest data-flow list declares runtime calls to `-`. The topology row supplies aliases and port surfaces used by the generated gateway and service references.

## 9. Architecture

- Diagram SVG: [`services/parakeet/architecture.svg`](https://github.com/thekaveh/atlas/blob/main/services/parakeet/architecture.svg)
- Diagram HTML: [`services/parakeet/architecture.html`](https://github.com/thekaveh/atlas/blob/main/services/parakeet/architecture.html)

## 10. Operations

Use `./start.sh` to configure this service through the wizard or pass the matching SOURCE flag when the service is source-configurable. Use `./stop.sh` to stop the active Atlas project.

## 11. Source Documentation

- Source README: [services/parakeet/README.md](https://github.com/thekaveh/atlas/blob/main/services/parakeet/README.md)
- Public docs home: [https://thekaveh.github.io/atlas/](https://thekaveh.github.io/atlas/)
