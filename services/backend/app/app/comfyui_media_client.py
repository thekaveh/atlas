"""ComfyUI-backed provider for the hosted media gateway (#519).

Mirrors :class:`fal_media_client.FalClient` so the gateway can route
``provider=comfyui, modality=image`` to the managed/host ComfyUI instance
behind the same submit/poll/cancel envelope as the FAL path.

The transport talks to the ComfyUI HTTP API directly (``POST /prompt``,
``GET /history/{id}``, ``GET /queue``, ``POST /queue`` + ``/interrupt``,
``POST /upload/image``, ``GET /view``) and raises on failure so the
gateway's ``try/except`` can map errors to HTTP codes the same way the
FAL client does. ``COMFYUI_BASE_URL`` is already plumbed into the backend
container (``services/backend/compose.yml`` → ``${COMFYUI_ENDPOINT}``).

Scope (see issue context): image generation only — text2img + img2img.
``image_to_3d`` stays on FAL. Local generation is genuinely free, so
``cost_usd`` is ``0.0`` (recorded for provenance, never ``None``).
"""
from __future__ import annotations

import base64
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx


# ────────────────────────────────────────────────────────────────────
# Graph profiles
# ────────────────────────────────────────────────────────────────────
# A single catalog model maps to one of two graph shapes:
#   "checkpoint" — a single-file SD1.5/SDXL checkpoint (CheckpointLoaderSimple)
#   "split"      — a multi-file bundle: diffusion UNet + CLIP + VAE loaded
#                  separately (Krea 2 / Flux-style families)
# The Krea 2 split graph mirrors the tested fixture at
# services/comfyui/workflows/krea2-turbo-api.json verbatim (CLIPLoader
# type="krea2", cfg=1.0, ConditioningZeroOut negative, euler/simple).
_DEFAULT_PROFILE: Dict[str, Any] = {
    "graph_kind": "checkpoint",
    "steps": 20,
    "cfg": 7.0,
    "sampler_name": "euler",
    "scheduler": "normal",
    "negative_strategy": "encode",  # real CLIPTextEncode negative
}

_KREA2_PROFILE: Dict[str, Any] = {
    "graph_kind": "split",
    # Krea 2 Turbo: 8 steps, cfg 1.0 (no real negative → ConditioningZeroOut),
    # euler/simple per the tested fixture.
    "steps": 8,
    "cfg": 1.0,
    "sampler_name": "euler",
    "scheduler": "simple",
    "negative_strategy": "zero_out",
    "clip_type": "krea2",
}

# Match by catalog-name substring (case-insensitive). Order matters: the
# first matching keyword wins. Unknown models fall back to _DEFAULT_PROFILE.
_PROFILE_KEYWORDS: List[Tuple[str, Dict[str, Any]]] = [
    ("krea", _KREA2_PROFILE),
]


def _profile_for(model: str) -> Dict[str, Any]:
    key = (model or "").strip().lower()
    for keyword, profile in _PROFILE_KEYWORDS:
        if keyword in key:
            return dict(profile)
    return dict(_DEFAULT_PROFILE)


def _resolve_param(input_payload: Dict[str, Any], key: str, aliases: Tuple[str, ...], profile: Dict[str, Any]) -> Any:
    """Caller-supplied value wins; otherwise the profile default."""
    for alias in aliases:
        if input_payload.get(alias) is not None:
            return input_payload[alias]
    return profile.get(key)


def _build_checkpoint_graph(
    *,
    model: str,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
    sampler_name: str,
    scheduler: str,
    denoise: float,
    init_image_name: Optional[str],
) -> Dict[str, Any]:
    """CheckpointLoaderSimple graph (SD1.5/SDXL). img2img swaps the latent
    source for LoadImage + VAEEncode and drives denoise from ``strength``."""
    graph: Dict[str, Any] = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["1", 1]}},
    }
    if init_image_name:
        graph["5"] = {"class_type": "LoadImage", "inputs": {"image": init_image_name}}
        graph["6"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["5", 0], "vae": ["1", 2]},
        }
        latent_ref: Any = ["6", 0]
    else:
        graph["5"] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        }
        latent_ref = ["5", 0]
    graph["4"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "denoise": denoise,
            "model": ["1", 0],
            "positive": ["2", 0],
            "negative": ["3", 0],
            "latent_image": latent_ref,
        },
    }
    graph["7"] = {"class_type": "VAEDecode", "inputs": {"samples": ["4", 0], "vae": ["1", 2]}}
    graph["8"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "Atlas_ComfyUI", "images": ["7", 0]}}
    return graph


def _build_split_graph(
    *,
    model: str,
    profile: Dict[str, Any],
    prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
    sampler_name: str,
    scheduler: str,
    denoise: float,
    init_image_name: Optional[str],
) -> Dict[str, Any]:
    """Multi-file bundle graph (Krea 2). Mirrors krea2-turbo-api.json:
    UNETLoader + CLIPLoader(type=krea2) + VAELoader, ConditioningZeroOut
    negative, VAEDecode from the standalone VAE. Node ids match the fixture
    so the live smoke (which keys outputs["9"]) stays valid.
    """
    # Resolve the per-role filenames from the model token. Krea 2 catalog
    # entries ship three files; the gateway accepts either a single alias
    # (resolved below) or explicit role keys in the input.
    inputs_model = model
    unet_name = (inputs_model or "").strip()
    clip_name = ""
    vae_name = ""
    # Caller may pass explicit role files (from the manifest bundle); these
    # override the single-alias default.
    role_files = _extract_role_files(inputs_model)
    if role_files:
        unet_name = role_files.get("diffusion") or role_files.get("unet") or unet_name
        clip_name = role_files.get("text_encoder") or role_files.get("clip") or clip_name
        vae_name = role_files.get("vae") or vae_name
    # Heuristic filename defaults for the canonical Krea 2 bundle when the
    # caller passes only a model alias. These match the curated catalog's
    # physical download filenames; a caller passing explicit role files wins.
    if not clip_name:
        clip_name = "qwen3vl_4b_bf16.safetensors"
    if not vae_name:
        vae_name = "qwen_image_vae.safetensors"

    graph: Dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet_name, "weight_dtype": "default"}},
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": clip_name,
                "type": profile.get("clip_type", "krea2"),
                "device": "default",
            },
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        # cfg=1.0 families have no real negative prompt → zero-out the
        # positive conditioning (matches the tested Krea 2 fixture).
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
    }
    if init_image_name:
        graph["6"] = {"class_type": "LoadImage", "inputs": {"image": init_image_name}}
        graph["10"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["6", 0], "vae": ["3", 0]},
        }
        latent_ref: Any = ["10", 0]
    else:
        graph["6"] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        }
        latent_ref = ["6", 0]
    graph["7"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler_name,
            "scheduler": scheduler,
            "denoise": denoise,
            "model": ["1", 0],
            "positive": ["4", 0],
            "negative": ["5", 0],
            "latent_image": latent_ref,
        },
    }
    graph["8"] = {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}}
    graph["9"] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "Atlas_Krea2_Turbo", "images": ["8", 0]}}
    return graph


_ROLE_FILE_RE = re.compile(r"(diffusion|unet|text_encoder|clip|vae)[=:]\s*([^\s,;]+)", re.IGNORECASE)


def _extract_role_files(model: str) -> Dict[str, str]:
    """Parse explicit per-role filenames from a ``model`` token like
    ``diffusion=krea2_turbo_bf16.safetensors,vae=qwen_image_vae.safetensors``.
    Lets a caller pass the full bundle without catalog coupling.
    """
    roles: Dict[str, str] = {}
    if not isinstance(model, str):
        return roles
    for key, value in _ROLE_FILE_RE.findall(model):
        roles[key.lower()] = value
    return roles


# ────────────────────────────────────────────────────────────────────
# Client
# ────────────────────────────────────────────────────────────────────
class ComfyUIMediaClient:
    """Async adapter submitting/polling/cancelling ComfyUI prompt jobs
    behind the gateway media-operation envelope."""

    SUPPORTED_MODALITIES = ("image",)

    def __init__(self, *, base_url: Optional[str] = None, model: Optional[str] = None) -> None:
        self.base_url = (base_url or os.getenv("COMFYUI_BASE_URL", "http://comfyui:18188")).rstrip("/")
        self.model = (model or "").strip()
        # Per-HTTP-call timeouts (not the generation-poll budget, which the
        # gateway owns via request.timeout_seconds). Fail fast on a down host
        # (connect=5); keep a 60s read budget for the slowest legitimate round.
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)
        )

    async def __aenter__(self) -> "ComfyUIMediaClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.client.aclose()

    # ── submit / poll / cancel ──────────────────────────────────────
    async def submit_media_operation(
        self,
        *,
        modality: str,
        input_payload: Dict[str, Any],
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        if modality not in self.SUPPORTED_MODALITIES:
            raise ValueError(f"Unsupported ComfyUI media modality: {modality}")

        selected_model = (model or self.model or "").strip()
        if not selected_model:
            raise ValueError("provider=comfyui requires a model (checkpoint filename or catalog name)")

        profile = _profile_for(selected_model)
        prompt = str(input_payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("provider=comfyui image input must include a non-empty prompt")

        nested_size = input_payload.get("image_size")
        if not isinstance(nested_size, dict):
            nested_size = {}
        width = int(input_payload.get("width") or nested_size.get("width") or 1024)
        height = int(input_payload.get("height") or nested_size.get("height") or 1024)
        steps = int(_resolve_param(input_payload, "steps", ("steps", "num_inference_steps"), profile))
        cfg = float(_resolve_param(input_payload, "cfg", ("cfg", "guidance_scale"), profile))
        sampler_name = str(_resolve_param(input_payload, "sampler_name", ("sampler_name",), profile))
        scheduler = str(_resolve_param(input_payload, "scheduler", ("scheduler",), profile))
        seed_input = input_payload.get("seed")
        seed = int(seed_input) if seed_input is not None else int.from_bytes(os.urandom(4), "big") % (2**32)

        # img2img (#453 parity): an init image under any of the accepted keys.
        init_image_name: Optional[str] = None
        init_source = _first_present(input_payload, ("image_url", "image", "init_image"))
        strength = _coerce_float(input_payload.get("strength"), default=0.75)
        if init_source:
            init_image_name = await self._upload_init_image(init_source)
            # strength=1.0 means "ignore the init image" (full denoise); the
            # graph still loads it but the result is effectively text2img.
            denoise = max(0.0, min(1.0, strength))
        else:
            denoise = 1.0

        negative_prompt = str(input_payload.get("negative_prompt") or "")
        if profile["graph_kind"] == "split":
            # cfg=1.0 families ignore the negative prompt (ConditioningZeroOut);
            # surface a clear signal rather than silently dropping it.
            graph = _build_split_graph(
                model=selected_model,
                profile=profile,
                prompt=prompt,
                width=width,
                height=height,
                steps=steps,
                cfg=cfg,
                seed=seed,
                sampler_name=sampler_name,
                scheduler=scheduler,
                denoise=denoise,
                init_image_name=init_image_name,
            )
        else:
            graph = _build_checkpoint_graph(
                model=selected_model,
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                steps=steps,
                cfg=cfg,
                seed=seed,
                sampler_name=sampler_name,
                scheduler=scheduler,
                denoise=denoise,
                init_image_name=init_image_name,
            )

        prompt_id = await self._queue_prompt(graph)
        return self._operation_payload(
            operation_id=prompt_id,
            status="queued",
            model=selected_model,
            modality=modality,
            raw={"prompt_id": prompt_id},
            parameters={
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "width": width,
                "height": height,
                "steps": steps,
                "cfg": cfg,
                "seed": seed,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "strength": strength if init_source else None,
                "graph_kind": profile["graph_kind"],
            },
        )

    async def get_media_operation(self, *, operation_id: str, modality: str) -> Dict[str, Any]:
        if modality not in self.SUPPORTED_MODALITIES:
            raise ValueError(f"Unsupported ComfyUI media modality: {modality}")

        history = await self._get_history(operation_id)
        entry = history.get(operation_id) if isinstance(history, dict) else None
        if entry is None:
            # Not yet in history → queued or running. Probe the live queue so
            # the consumer sees an honest in-progress status (not "failed").
            queue = await self._get_queue()
            status = self._queue_status(operation_id, queue)
            return self._operation_payload(
                operation_id=operation_id,
                status=status,
                model=self.model,
                modality=modality,
                raw={"history": None, "queue": queue},
            )

        status_str, error_msg = self._history_status(entry)
        # Only a completed-with-outputs entry is "succeeded"; an "error" status
        # is "failed"; anything else (partial/interrupted history, or a status
        # ComfyUI writes mid-execution) stays "running" so the gateway keeps
        # polling instead of caching a succeeded-but-artifactless terminal.
        if status_str == "error":
            normalized = "failed"
        elif status_str == "success":
            normalized = "succeeded"
        else:
            normalized = "running"
        payload = self._operation_payload(
            operation_id=operation_id,
            status=normalized,
            model=self.model,
            modality=modality,
            raw={"history": entry},
        )
        if normalized == "succeeded":
            artifacts = self._extract_artifacts(entry)
            payload["artifacts"] = artifacts
            payload["artifact_url"] = artifacts[0]["url"] if artifacts else None
            payload["raw"]["error"] = None
        elif normalized == "failed":
            payload["raw"]["error"] = error_msg
        return payload

    async def cancel_media_operation(self, *, operation_id: str, modality: str) -> bool:
        """Best-effort provider cancel (#518 parity): drop the prompt from
        the pending queue and interrupt it only if currently running.
        Returns False on any transport failure — the gateway still marks the
        op terminal ``cancelled`` server-side and releases the reservation."""
        if modality not in self.SUPPORTED_MODALITIES:
            raise ValueError(f"Unsupported ComfyUI media modality: {modality}")
        try:
            queue = await self._get_queue()
            running_ids = {
                item[1]
                for item in queue.get("queue_running", [])
                if isinstance(item, (list, tuple)) and len(item) > 1
            }
            resp = await self.client.post(f"{self.base_url}/queue", json={"delete": [operation_id]})
            resp.raise_for_status()
            if operation_id in running_ids:
                resp = await self.client.post(f"{self.base_url}/interrupt")
                resp.raise_for_status()
            return True
        except Exception:  # noqa: BLE001 — best-effort by contract
            return False

    # ── ComfyUI HTTP primitives ─────────────────────────────────────
    async def _queue_prompt(self, workflow: Dict[str, Any]) -> str:
        client_id = str(uuid.uuid4())
        resp = await self.client.post(
            f"{self.base_url}/prompt",
            json={"prompt": workflow, "client_id": client_id},
        )
        if resp.status_code >= 400:
            detail = self._error_detail(resp)
            if resp.status_code < 500:
                # A 4xx is a client error (bad graph / unknown model /
                # node_errors) → ValueError so the gateway maps it to 400,
                # not a 502 that implies the host is down.
                raise ValueError(f"ComfyUI rejected the prompt: {detail}")
            # A 5xx is a host-side failure → HTTPStatusError → gateway 502.
            raise httpx.HTTPStatusError(detail, request=resp.request, response=resp)
        body = resp.json()
        prompt_id = body.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI accepted the prompt but returned no prompt_id: {body}")
        return str(prompt_id)

    async def _get_history(self, prompt_id: str) -> Dict[str, Any]:
        resp = await self.client.get(f"{self.base_url}/history/{prompt_id}")
        if resp.status_code == 404:
            return {}
        self._raise_for_status(resp)
        body = resp.json()
        return body if isinstance(body, dict) else {}

    async def _get_queue(self) -> Dict[str, Any]:
        resp = await self.client.get(f"{self.base_url}/queue")
        self._raise_for_status(resp)
        body = resp.json()
        return body if isinstance(body, dict) else {}

    async def _upload_init_image(self, source: str) -> str:
        """Fetch init-image bytes (URL or data URI) and push them into
        ComfyUI's ``input/`` dir via ``POST /upload/image``. Returns the
        stored filename to feed ``LoadImage.inputs.image``."""
        content = await self._fetch_image_bytes(source)
        # Derive a collision-free filename so concurrent img2img requests
        # never clobber each other (overwrite=true would race; a uuid name
        # sidesteps it). Preserve the caller's extension when present.
        ext = _guess_extension(source, content) or "png"
        filename = f"atlas-init-{uuid.uuid4().hex}.{ext}"
        files = {"image": (filename, content, "application/octet-stream")}
        data = {"overwrite": "true", "type": "input"}
        resp = await self.client.post(f"{self.base_url}/upload/image", files=files, data=data)
        self._raise_for_status(resp)
        body = resp.json()
        name = body.get("name") or filename
        subfolder = body.get("subfolder") or ""
        # LoadImage expects "<subfolder>/<name>" when a subfolder is used.
        return f"{subfolder}/{name}" if subfolder else str(name)

    async def _fetch_image_bytes(self, source: str) -> bytes:
        source = source.strip()
        if source.lower().startswith("data:"):
            # data URI: data:<mime>;base64,<payload>
            try:
                _, _, b64 = source.partition(",")
                return base64.b64decode(b64)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"Could not decode data-URI init image: {exc}") from exc
        if not source.startswith(("http://", "https://")):
            raise ValueError(
                "ComfyUI init image must be an http(s) URL or a data: URI "
                f"(got: {source[:32]}…)"
            )
        try:
            resp = await self.client.get(source)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            raise ValueError(f"Could not fetch ComfyUI init image from {source}: {exc}") from exc

    @staticmethod
    def _error_detail(resp: httpx.Response) -> str:
        """Extract a human-readable detail from a ComfyUI error response
        (``error.message`` + ``node_errors``), tolerating non-JSON bodies."""
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            return resp.text or resp.reason_phrase
        message = (
            body.get("error", {}).get("message")
            if isinstance(body, dict) and isinstance(body.get("error"), dict)
            else None
        )
        node_errors = body.get("node_errors") if isinstance(body, dict) else None
        detail = message or resp.text or resp.reason_phrase
        if node_errors:
            return f"{detail} | node_errors={node_errors}"
        return detail

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        raise httpx.HTTPStatusError(
            ComfyUIMediaClient._error_detail(resp),
            request=resp.request,
            response=resp,
        )

    # ── status / artifact normalization ────────────────────────────
    @staticmethod
    def _history_status(entry: Dict[str, Any]) -> Tuple[str, Optional[str]]:
        status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
        status_str = str(status.get("status_str") or "").strip().lower()
        error_msg = None
        for msg in status.get("messages", []) or []:
            # messages are [type, {details}]; execution_error carries the
            # exception text. Keep this tolerant of shape drift (live-verify).
            if isinstance(msg, (list, tuple)) and len(msg) >= 2 and msg[0] == "execution_error":
                details = msg[1] if isinstance(msg[1], dict) else {}
                error_msg = str(
                    details.get("exception_message")
                    or details.get("exception_type")
                    or details
                )
                status_str = "error"
        if not status_str:
            status_str = "success" if entry.get("outputs") else "running"
        return status_str, error_msg

    @staticmethod
    def _queue_status(prompt_id: str, queue: Dict[str, Any]) -> str:
        for item in queue.get("queue_running", []) or []:
            if isinstance(item, (list, tuple)) and len(item) > 1 and item[1] == prompt_id:
                return "running"
        for item in queue.get("queue_pending", []) or []:
            if isinstance(item, (list, tuple)) and len(item) > 1 and item[1] == prompt_id:
                return "queued"
        # Absent from both history and queue — the prompt was lost (restart,
        # eviction, …). Report failed so the gateway settles the ledger.
        return "failed"

    @staticmethod
    def _extract_artifacts(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
        outputs = entry.get("outputs") if isinstance(entry.get("outputs"), dict) else {}
        artifacts: List[Dict[str, Any]] = []
        for node_out in outputs.values():
            if not isinstance(node_out, dict):
                continue
            for image in node_out.get("images", []) or []:
                if not isinstance(image, dict) or not image.get("filename"):
                    continue
                filename = str(image["filename"])
                subfolder = str(image.get("subfolder") or "")
                folder_type = str(image.get("type") or "output")
                # artifact_url is a backend-relative proxy path (Kong-routable,
                # same GET /comfyui/image/{filename} open-webui/n8n use) — NOT
                # a fal-style absolute hosted URL. Local consumers are in-network.
                params = {"subfolder": subfolder, "folder_type": folder_type}
                query = "&".join(f"{k}={v}" for k, v in params.items() if v)
                url = f"/comfyui/image/{filename}"
                if query:
                    url = f"{url}?{query}"
                artifacts.append(
                    {
                        "url": url,
                        "role": "image",
                        "content_type": _content_type_for(filename),
                        "filename": filename,
                        "subfolder": subfolder,
                        "folder_type": folder_type,
                    }
                )
        return artifacts

    def _operation_payload(
        self,
        *,
        operation_id: str,
        status: str,
        model: str,
        modality: str,
        raw: Dict[str, Any],
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # Local generation is genuinely free — record 0.0 (never None, which
        # the budget engine treats as "unknown cost" and may reject).
        return {
            "operation_id": operation_id,
            "status": status,
            "provider": "comfyui",
            "model": model or self.model,
            "modality": modality,
            "artifact_url": None,
            "artifacts": [],
            "cost_usd": 0.0,
            "license": "local/self-hosted",
            "provenance": {
                "provider_request_id": operation_id,
                "modality": modality,
                "cost_basis": "local_zero",
            },
            "raw": raw,
            **({"parameters": parameters} if parameters else {}),
        }


# ────────────────────────────────────────────────────────────────────
# Small helpers (module-level so they're unit-testable without a client)
# ────────────────────────────────────────────────────────────────────
def _first_present(payload: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _guess_extension(source: str, content: bytes) -> Optional[str]:
    lower = source.lower().split("?", 1)[0]
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        if lower.endswith(ext):
            return ext.lstrip(".")
    # Sniff the magic bytes when the URL had no usable extension.
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if content[:3] == b"\xff\xd8\xff":
        return "jpg"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return None


def _content_type_for(filename: str) -> str:
    lower = (filename or "").lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/png"
