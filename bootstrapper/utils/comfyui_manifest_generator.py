"""
ComfyUI manifest generator.

Writes three files into ``volumes/comfyui/`` at bootstrapper start (when ComfyUI
is enabled), replacing the former ``public.comfyui_models`` DB query that
``comfyui-init`` ran at container startup:

  • ``volumes/comfyui/selected-models.yaml``
        Full YAML manifest (``{"models": [...]}``), validated against
        ``bootstrapper/schemas/comfyui-manifest.schema.json``.  This is the
        canonical active-model file; the backend ``GET /comfyui/db/models``
        (C4) reads it directly.

  • ``volumes/comfyui/active-models.tsv``
        Shell-consumable tab-separated view: ``name\\ttype\\tfilename\\t
        download_url\\tsha256\\ttarget_dir`` (one row per active model file,
        no header).
        ``sha256`` is the empty string when ``None`` — matching the old
        ``COALESCE(sha256, '')`` pattern — so the existing verification
        branch in ``download_models.sh`` continues to work unchanged.
        ``comfyui-init``'s ``download_models.sh`` ``cat``s this file into
        its existing tempfile/download loop.

  • ``volumes/comfyui/active-custom-nodes.tsv``
        Shell-consumable tab-separated install plan for allowlisted custom
        nodes required by the active model set:
        ``name\\trepo\\tref\\tinstall_requirements``.

All generated files are written atomically (tmp-then-replace) via
``comfyui_resolver.write_manifest`` (YAML) and an inline atomic write (TSV).

The generator is skipped cleanly when ``COMFYUI_SOURCE == "disabled"``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


class ComfyUIManifestGenerator:
    """Writes ``volumes/comfyui/selected-models.yaml``,
    ``volumes/comfyui/active-models.tsv``, and
    ``volumes/comfyui/active-custom-nodes.tsv`` from the resolved active set.

    Mirrors the structure of ``LiteLLMConfigGenerator``; consumed by
    ``AtlasStarter.generate_comfyui_manifest()`` in ``start.py``.
    """

    def __init__(self, env: Mapping[str, str]):
        """
        Args:
            env: Current env mapping (from config_parser or .env).  Must
                contain ``COMFYUI_SOURCE`` and optionally
                ``COMFYUI_USER_MODELS`` / ``COMFYUI_CUSTOM_MODELS_FILE``.
        """
        self.env = env

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """Return True when ComfyUI is active (SOURCE != disabled)."""
        return self.env.get("COMFYUI_SOURCE", "disabled") != "disabled"

    def write(self, output_dir: Path) -> bool:
        """Resolve the active model set and write both output files.

        Uses ``comfyui_resolver.active_comfyui_models(env)`` (C2) — pure host-
        side resolver, no DB, no Docker.  Files are written atomically so a
        crashed bootstrapper never leaves a partial file on disk.

        Args:
            output_dir: Directory for the output files; typically
                ``<repo-root>/volumes/comfyui/``.  Created if absent.

        Returns:
            True on success (files written or ComfyUI disabled — both are
            normal outcomes).  Callers should treat False as a fatal error.
        """
        if not self.is_enabled():
            return True  # skipped cleanly

        output_dir.mkdir(parents=True, exist_ok=True)

        # --- resolve active entries (no DB, no network scrape in tests) ---
        from utils import comfyui_resolver

        entries = comfyui_resolver.active_comfyui_models(self.env)

        # --- write YAML manifest (canonical SoT, read by backend C4) ---
        yaml_path = output_dir / "selected-models.yaml"
        comfyui_resolver.write_manifest(entries, str(yaml_path))

        # --- write TSV (shell-consumable view for download_models.sh) ---
        tsv_path = output_dir / "active-models.tsv"
        self._write_tsv(entries, tsv_path)

        # --- write custom-node TSV (shell-consumable install plan) ---
        custom_nodes_tsv_path = output_dir / "active-custom-nodes.tsv"
        self._write_custom_nodes_tsv(entries, custom_nodes_tsv_path)

        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_tsv_field(row: dict[str, Any], key: str) -> str:
        value = str(row[key])
        if "\t" in value or "\n" in value or "\r" in value:
            raise ValueError(
                f"ComfyUI model field {key!r} for {row.get('name', '<unknown>')!r} "
                "contains a tab or newline; cannot write active-models.tsv safely."
            )
        return value

    @staticmethod
    def _safe_filename(row: dict[str, Any]) -> str:
        filename = ComfyUIManifestGenerator._safe_tsv_field(row, "filename")
        if (
            not filename
            or filename != os.path.basename(filename)
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
        ):
            raise ValueError(
                f"ComfyUI model filename for {row.get('name', '<unknown>')!r} "
                f"must be a plain basename, got {filename!r}."
            )
        return filename

    @staticmethod
    def _row_tsv(row: dict[str, Any]) -> str:
        """Format a manifest-dict row as a single TSV line.

        Columns: name TAB type TAB filename TAB download_url TAB sha256 TAB target_dir
        ``sha256`` is the empty string when ``None``, matching the old
        ``COALESCE(sha256, '')`` pattern used by the former psql query.
        """
        sha = row.get("sha256") or ""  # None → ""
        if "\t" in str(sha) or "\n" in str(sha) or "\r" in str(sha):
            raise ValueError(
                f"ComfyUI model sha256 for {row.get('name', '<unknown>')!r} "
                "contains a tab or newline; cannot write active-models.tsv safely."
            )
        return "\t".join([
            ComfyUIManifestGenerator._safe_tsv_field(row, "name"),
            ComfyUIManifestGenerator._safe_tsv_field(row, "type"),
            ComfyUIManifestGenerator._safe_filename(row),
            ComfyUIManifestGenerator._safe_tsv_field(row, "download_url"),
            str(sha),
            ComfyUIManifestGenerator._safe_tsv_field(row, "target_dir"),
        ])

    @staticmethod
    def _safe_custom_node_field(name: str, value: object) -> str:
        rendered = str(value)
        if "\t" in rendered or "\n" in rendered or "\r" in rendered:
            raise ValueError(
                f"ComfyUI custom-node field for {name!r} contains a tab or newline; "
                "cannot write active-custom-nodes.tsv safely."
            )
        return rendered

    @staticmethod
    def _custom_node_row_tsv(node: Any) -> str:
        return "\t".join([
            ComfyUIManifestGenerator._safe_custom_node_field(node.name, node.name),
            ComfyUIManifestGenerator._safe_custom_node_field(node.name, node.repo),
            ComfyUIManifestGenerator._safe_custom_node_field(node.name, node.ref),
            "true" if node.install_requirements else "false",
        ])

    def _write_tsv(
        self,
        entries: list[Any],
        tsv_path: Path,
    ) -> None:
        """Write the tab-separated active-models file atomically.

        The file has no header row — ``download_models.sh`` reads each row and
        splits it with ``cut -f`` (which preserves empty interior fields such as
        a no-checksum ``sha`` column).

        An empty entries list produces an empty file (zero bytes), which
        ``download_models.sh`` detects via ``[ ! -s "$MANIFEST_TSV" ]`` and
        treats as "nothing to download" — the same early-exit path as the old
        "no active comfyui_models rows" branch.
        """
        from utils import comfyui_resolver

        manifest = comfyui_resolver.manifest_dict(entries)
        rows = manifest.get("models", [])

        # Multiple logical bundles may share one physical artifact. Keep each
        # bundle row in selected-models.yaml, but download a target path once.
        # A metadata disagreement for the same path is a catalog error, not a
        # last-writer-wins situation.
        download_rows: list[dict[str, Any]] = []
        seen_paths: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = (str(row["target_dir"]), str(row["filename"]))
            previous = seen_paths.get(key)
            if previous is not None:
                for field in (
                    "type",
                    "download_url",
                    "sha256",
                    "file_size_bytes",
                    "file_size_gb",
                ):
                    if previous.get(field) != row.get(field):
                        raise ValueError(
                            "Conflicting ComfyUI download metadata for "
                            f"{key[0]}/{key[1]}: {field} differs between "
                            f"{previous.get('bundle_id') or previous.get('name')} and "
                            f"{row.get('bundle_id') or row.get('name')}."
                        )
                continue
            seen_paths[key] = row
            download_rows.append(row)

        tsv_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(tsv_path.parent),
            prefix=tsv_path.name + ".",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for row in download_rows:
                    fh.write(self._row_tsv(row) + "\n")
            os.replace(tmp, str(tsv_path))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _write_custom_nodes_tsv(
        self,
        entries: list[Any],
        tsv_path: Path,
    ) -> None:
        """Write the tab-separated active custom-node install plan atomically."""
        from utils import comfyui_custom_nodes

        nodes = comfyui_custom_nodes.active_custom_nodes(entries, self.env)

        tsv_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(tsv_path.parent),
            prefix=tsv_path.name + ".",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for node in nodes:
                    fh.write(self._custom_node_row_tsv(node) + "\n")
            os.replace(tmp, str(tsv_path))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
