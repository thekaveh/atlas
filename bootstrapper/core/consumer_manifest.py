"""Consumer manifest loading for parent-owned Atlas integrations."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml


class ConsumerManifestError(ValueError):
    """Raised when one or more consumer manifests are invalid."""


@dataclass(frozen=True)
class ConsumerRecord:
    name: str
    manifest_path: Path
    compose_overlays: tuple[Path, ...] = ()
    backend_plugins: tuple[Path, ...] = ()
    comfyui_sidecars: tuple[Path, ...] = ()
    ollama_models: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConsumerConfig:
    consumers: tuple[ConsumerRecord, ...] = ()
    env_overrides: dict[str, str] = field(default_factory=dict)
    compose_overlays: list[Path] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.consumers


_BRAND_ENV_MAP = {
    "name": "BRAND_NAME",
    "tagline": "BRAND_TAGLINE",
    "version": "BRAND_VERSION",
    "author": "BRAND_AUTHOR",
    "author_email": "BRAND_AUTHOR_EMAIL",
    "license": "BRAND_LICENSE",
    "repo_url": "BRAND_REPO_URL",
    "logo_file": "BRAND_LOGO_FILE",
}


def _invoker_relative_base(root_dir: Path) -> Path:
    invoker = os.environ.get("ATLAS_INVOKER_CWD", "").strip()
    if invoker:
        return Path(invoker).expanduser()
    return root_dir


def _split_manifest_env(raw: str) -> list[str]:
    pieces: list[str] = []
    for part in raw.split(os.pathsep):
        for item in part.split(","):
            item = item.strip()
            if item:
                pieces.append(item)
    return pieces


def discover_consumer_manifest_paths(
    root_dir: Path | str,
    *,
    explicit_paths: Iterable[str] | None = None,
) -> list[Path]:
    """Resolve consumer manifest paths from CLI paths or environment."""
    root = Path(root_dir)
    raw_paths = list(explicit_paths or [])
    if not raw_paths:
        env_value = os.environ.get("ATLAS_CONSUMER_MANIFEST", "").strip()
        raw_paths = _split_manifest_env(env_value) if env_value else []

    base_dir = _invoker_relative_base(root)
    resolved: list[Path] = []
    for raw in raw_paths:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        resolved.append(path.resolve())
    return resolved


def _read_env_overlay(path: Path) -> dict[str, str]:
    env_vars: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if value[:1] in ('"', "'"):
            quote = value[0]
            end = value.find(quote, 1)
            if end != -1:
                value = value[1:end]
            else:
                value = value.strip('"').strip("'")
        else:
            for i, ch in enumerate(value):
                if ch == "#" and (i == 0 or value[i - 1] in " \t"):
                    value = value[:i]
                    break
            value = value.strip()
        env_vars[key.strip()] = value
    return env_vars


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _resolve_existing_file(base_dir: Path, raw_path: str, *, label: str) -> Path:
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    if not path.is_file():
        raise ConsumerManifestError(f"{label} does not exist or is not a file: {path}")
    return path


def _resolve_existing_dir(base_dir: Path, raw_path: str, *, label: str) -> Path:
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve()
    if not path.is_dir():
        raise ConsumerManifestError(f"{label} does not exist or is not a directory: {path}")
    return path


def _set_scalar(
    env: dict[str, str],
    origins: dict[str, str],
    key: str,
    value: Any,
    origin: str,
) -> None:
    rendered = str(value)
    if key in env and env[key] != rendered:
        raise ConsumerManifestError(
            f"{key} has conflicting consumer manifest values: "
            f"{origins[key]}={env[key]!r}, {origin}={rendered!r}"
        )
    env[key] = rendered
    origins[key] = origin


def _ordered_union(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = value.strip()
        if not value or value in seen:
            continue
        out.append(value)
        seen.add(value)
    return out


def _load_manifest(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise ConsumerManifestError(f"consumer manifest does not exist: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConsumerManifestError(f"could not parse consumer manifest {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ConsumerManifestError(f"consumer manifest must be a mapping: {path}")
    return data


def load_consumer_config(
    root_dir: Path | str,
    *,
    explicit_paths: Iterable[str] | None = None,
) -> ConsumerConfig:
    """Load and validate all configured consumer manifests."""
    root = Path(root_dir)
    manifest_paths = discover_consumer_manifest_paths(root, explicit_paths=explicit_paths)
    if not manifest_paths:
        return ConsumerConfig()

    env_overrides: dict[str, str] = {}
    env_origins: dict[str, str] = {}
    consumers: list[ConsumerRecord] = []
    compose_overlays: list[Path] = []
    backend_plugins: list[Path] = []
    comfyui_sidecars: list[Path] = []
    ollama_models: list[str] = []

    for manifest_path in manifest_paths:
        data = _load_manifest(manifest_path)
        base_dir = manifest_path.parent
        consumer_name = str(data.get("name") or manifest_path.parent.name)
        origin = str(manifest_path)

        if project_name := data.get("project_name"):
            _set_scalar(env_overrides, env_origins, "PROJECT_NAME", project_name, origin)

        brand = data.get("brand") or {}
        if brand:
            if not isinstance(brand, Mapping):
                raise ConsumerManifestError(f"brand must be a mapping in {manifest_path}")
            for key, env_key in _BRAND_ENV_MAP.items():
                if key in brand and brand[key] is not None:
                    value = brand[key]
                    if key.endswith("_file"):
                        value_path = Path(str(value)).expanduser()
                        if not value_path.is_absolute():
                            value = str((base_dir / value_path).resolve())
                    _set_scalar(env_overrides, env_origins, env_key, value, origin)

        env_block = data.get("env") or {}
        if env_block:
            if not isinstance(env_block, Mapping):
                raise ConsumerManifestError(f"env must be a mapping in {manifest_path}")
            for raw_file in _as_list(env_block.get("file")):
                env_path = _resolve_existing_file(base_dir, str(raw_file), label="env.file")
                for key, value in _read_env_overlay(env_path).items():
                    _set_scalar(env_overrides, env_origins, key, value, str(env_path))
            values = env_block.get("values") or {}
            if values:
                if not isinstance(values, Mapping):
                    raise ConsumerManifestError(f"env.values must be a mapping in {manifest_path}")
                for key, value in values.items():
                    _set_scalar(env_overrides, env_origins, str(key), value, origin)

        record_overlays: list[Path] = []
        for raw_overlay in _as_list(data.get("compose_overlays")):
            overlay = _resolve_existing_file(
                base_dir, str(raw_overlay), label="compose_overlays entry"
            )
            if overlay not in compose_overlays:
                compose_overlays.append(overlay)
            record_overlays.append(overlay)

        record_plugins: list[Path] = []
        for raw_plugins in _as_list(data.get("backend_plugins")):
            plugin_dir = _resolve_existing_dir(
                base_dir, str(raw_plugins), label="backend_plugins entry"
            )
            if plugin_dir not in backend_plugins:
                backend_plugins.append(plugin_dir)
            record_plugins.append(plugin_dir)

        model_sidecars = data.get("model_sidecars") or {}
        record_comfyui: list[Path] = []
        record_ollama: list[str] = []
        if model_sidecars:
            if not isinstance(model_sidecars, Mapping):
                raise ConsumerManifestError(f"model_sidecars must be a mapping in {manifest_path}")
            for raw_sidecar in _as_list(model_sidecars.get("comfyui")):
                sidecar = _resolve_existing_file(
                    base_dir, str(raw_sidecar), label="model_sidecars.comfyui entry"
                )
                if sidecar not in comfyui_sidecars:
                    comfyui_sidecars.append(sidecar)
                record_comfyui.append(sidecar)
            for raw_model in _as_list(model_sidecars.get("ollama")):
                model = str(raw_model).strip()
                if model and model not in ollama_models:
                    ollama_models.append(model)
                if model:
                    record_ollama.append(model)

        consumers.append(
            ConsumerRecord(
                name=consumer_name,
                manifest_path=manifest_path,
                compose_overlays=tuple(record_overlays),
                backend_plugins=tuple(record_plugins),
                comfyui_sidecars=tuple(record_comfyui),
                ollama_models=tuple(_ordered_union(record_ollama)),
            )
        )

    if backend_plugins:
        env_overrides["BACKEND_PLUGINS_DIR"] = os.pathsep.join(str(path) for path in backend_plugins)
    if comfyui_sidecars:
        env_overrides["COMFYUI_CUSTOM_MODELS_FILE"] = os.pathsep.join(
            str(path) for path in comfyui_sidecars
        )
    if ollama_models:
        env_overrides["OLLAMA_CUSTOM_MODELS"] = ",".join(_ordered_union(ollama_models))

    return ConsumerConfig(
        consumers=tuple(consumers),
        env_overrides=env_overrides,
        compose_overlays=compose_overlays,
    )
