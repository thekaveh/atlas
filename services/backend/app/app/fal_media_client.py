from __future__ import annotations

import asyncio
import os
import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple

import media_registry


_FAL_ENV_LOCK = threading.Lock()


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


class FalClient:
    """Small async wrapper around the blocking fal-client SDK."""

    # Media modalities this client can submit/poll through the fal queue.
    SUPPORTED_MODALITIES = ("image", "image_to_3d")

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        output_format: Optional[str] = None,
        enable_safety_checker: Optional[bool] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.api_key = (api_key or os.getenv("FAL_API_KEY") or os.getenv("FAL_KEY") or "").strip()
        self.model = (model or os.getenv("FAL_MODEL") or "fal-ai/flux/dev").strip()
        self.output_format = (output_format or os.getenv("FAL_OUTPUT_FORMAT") or "jpeg").strip()
        if timeout_seconds is None:
            try:
                self.timeout_seconds = float(
                    os.getenv("FAL_TIMEOUT_SECONDS", "120") or "120"
                )
            except ValueError:
                self.timeout_seconds = 120.0
        else:
            self.timeout_seconds = float(timeout_seconds)
        if self.timeout_seconds <= 0:
            raise ValueError("FAL timeout must be positive")
        self.enable_safety_checker = (
            _env_bool("FAL_ENABLE_SAFETY_CHECKER", True)
            if enable_safety_checker is None
            else enable_safety_checker
        )
        self.license = (os.getenv("FAL_MODEL_LICENSE") or "fal/provider-terms").strip()

    async def __aenter__(self) -> "FalClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def generate_simple_image(
        self,
        *,
        prompt: str,
        negative_prompt: str = "",
        width: int = 512,
        height: int = 512,
        steps: int = 20,
        cfg: float = 7.0,
        seed: Optional[int] = None,
        checkpoint: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.api_key:
            raise ValueError("FAL_API_KEY is required when FAL_SOURCE=enabled")

        arguments: Dict[str, Any] = {
            "prompt": prompt,
            "image_size": {"width": width, "height": height},
            "num_inference_steps": steps,
            "guidance_scale": cfg,
            "num_images": 1,
            "enable_safety_checker": self.enable_safety_checker,
            "output_format": self.output_format,
        }
        if seed is not None:
            arguments["seed"] = seed
        if negative_prompt:
            # flux-family endpoints accept negative_prompt; only send it when
            # the caller provided one, so models without the field still work
            # (and the empty case matches the exact-arguments test contract).
            arguments["negative_prompt"] = negative_prompt

        result = await self._call_blocking_with_timeout(
            self._subscribe, arguments
        )
        request_id = (
            result.get("request_id")
            or result.get("requestId")
            or result.get("id")
            or f"fal-{uuid.uuid4()}"
        )
        outputs = {"images": result.get("images", [])}

        return {
            "success": True,
            "prompt_id": request_id,
            "client_id": "fal",
            "outputs": outputs,
            "raw": result,
            "parameters": {
                "provider": "fal",
                "model": self.model,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "width": width,
                "height": height,
                "steps": steps,
                "cfg": cfg,
                "seed": result.get("seed", seed),
                "checkpoint": checkpoint,
                "output_format": self.output_format,
                "enable_safety_checker": self.enable_safety_checker,
            },
        }

    def _subscribe(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        import fal_client  # type: ignore[import-not-found]

        return self._sdk_call(
            fal_client, "subscribe", self.model, arguments=arguments
        )

    async def submit_media_operation(
        self,
        *,
        modality: str,
        input: Dict[str, Any],
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        if modality not in self.SUPPORTED_MODALITIES:
            raise ValueError(f"Unsupported FAL media modality: {modality}")
        if not self.api_key:
            raise ValueError("FAL_API_KEY is required when FAL_SOURCE=enabled")

        selected_model = (model or self.model).strip()
        if modality == "image_to_3d":
            arguments = self._image_to_3d_arguments(input)
        else:
            arguments = self._image_arguments(input)
        submitted = await self._call_blocking_with_timeout(
            self._submit, selected_model, arguments
        )
        operation_id = self._extract_request_id(submitted)

        return self._operation_payload(
            operation_id=operation_id,
            status="submitted",
            model=selected_model,
            modality=modality,
            raw=self._object_to_dict(submitted),
        )

    async def get_media_operation(self, *, operation_id: str, modality: str) -> Dict[str, Any]:
        if modality not in self.SUPPORTED_MODALITIES:
            raise ValueError(f"Unsupported FAL media modality: {modality}")
        if not self.api_key:
            raise ValueError("FAL_API_KEY is required when FAL_SOURCE=enabled")

        status_payload = await self._call_blocking_with_timeout(
            self._status, self.model, operation_id
        )
        normalized_status = self._normalize_status(status_payload)
        result_payload: Dict[str, Any] = {}
        if normalized_status == "succeeded":
            result_payload = await self._call_blocking_with_timeout(
                self._result, self.model, operation_id
            )

        raw = {
            "status": self._object_to_dict(status_payload),
            "result": self._object_to_dict(result_payload),
        }
        payload = self._operation_payload(
            operation_id=operation_id,
            status=normalized_status,
            model=self.model,
            modality=modality,
            raw=raw,
        )
        if result_payload:
            if modality == "image_to_3d":
                artifacts, provider_fields = self._extract_glb_artifacts(
                    result_payload, self.model
                )
                if provider_fields:
                    payload["provenance"]["provider_fields"] = provider_fields
                payload["artifacts"] = artifacts
                # The normalized artifact_url is the GLB specifically — never a
                # shadowing preview image when the GLB key is absent.
                glb = next(
                    (a for a in artifacts if a.get("role") == "model_glb"), None
                )
                payload["artifact_url"] = glb["url"] if glb else None
            else:
                artifacts = self._extract_artifacts(result_payload)
                payload["artifacts"] = artifacts
                payload["artifact_url"] = artifacts[0]["url"] if artifacts else None
            payload["raw"] = result_payload
        return payload

    def _image_arguments(self, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        # Size: flat width/height keys win; a nested `image_size` object is
        # accepted as a fallback (#453 — previously it was silently ignored
        # and the request defaulted to 512×512).
        nested_size = input_payload.get("image_size")
        if not isinstance(nested_size, dict):
            nested_size = {}
        width = int(input_payload.get("width") or nested_size.get("width") or 512)
        height = int(input_payload.get("height") or nested_size.get("height") or 512)
        steps = int(input_payload.get("steps") or 20)
        cfg = float(input_payload.get("cfg") or input_payload.get("guidance_scale") or 7.0)
        arguments: Dict[str, Any] = {
            "prompt": input_payload["prompt"],
            "image_size": {"width": width, "height": height},
            "num_inference_steps": steps,
            "guidance_scale": cfg,
            "num_images": int(input_payload.get("num_images") or 1),
            "enable_safety_checker": self.enable_safety_checker,
            "output_format": self.output_format,
        }
        if input_payload.get("seed") is not None:
            arguments["seed"] = input_payload["seed"]
        if input_payload.get("negative_prompt"):
            arguments["negative_prompt"] = input_payload["negative_prompt"]
        # img2img pass-through (#453): forward an init image (accepted under
        # image_url / image / init_image) to FAL's img2img key — the same
        # `image_url` convention _image_to_3d_arguments uses — plus the
        # optional `strength` denoise knob. Previously these were silently
        # dropped, degrading every img2img request to text2img.
        init_image = None
        for key in ("image_url", "image", "init_image"):
            value = input_payload.get(key)
            if isinstance(value, str) and value.strip():
                init_image = value.strip()
                break
        if init_image is not None:
            arguments["image_url"] = init_image
            if input_payload.get("strength") is not None:
                arguments["strength"] = float(input_payload["strength"])
        return arguments

    def _image_to_3d_arguments(self, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        image = input_payload.get("image")
        if not isinstance(image, str) or not image.strip():
            raise ValueError(
                "image_to_3d input requires a non-empty 'image' (URL or data URI)"
            )
        # fal's 3D API takes the input image under `image_url`; the gateway has
        # already hosted/conditioned it, so this is a URL or an accepted data URI.
        arguments: Dict[str, Any] = {"image_url": image.strip()}
        if input_payload.get("seed") is not None:
            arguments["seed"] = input_payload["seed"]
        # Optional, provider-tolerant passthroughs (each endpoint ignores keys
        # it does not recognize).
        for flag in (
            "texture",
            "pbr",
            "texture_size",
            "face_limit",
            "guidance_scale",
            "num_inference_steps",
            "quad",
        ):
            if input_payload.get(flag) is not None:
                arguments[flag] = input_payload[flag]
        extra = input_payload.get("extra")
        if isinstance(extra, dict):
            arguments.update(extra)
        return arguments

    def _submit(self, model: str, arguments: Dict[str, Any]) -> Any:
        import fal_client  # type: ignore[import-not-found]

        return self._sdk_call(
            fal_client, "submit", model, arguments=arguments
        )

    def _status(self, model: str, operation_id: str) -> Any:
        import fal_client  # type: ignore[import-not-found]

        return self._sdk_call(fal_client, "status", model, operation_id)

    async def cancel_media_operation(self, *, operation_id: str, modality: str) -> bool:
        """Best-effort provider-side cancel of an in-flight operation (#518).

        Returns True when the provider accepted the cancel, False when the
        cancel could not be delivered (SDK without ``cancel``, network error,
        already-settled request, …). Callers treat False as a safe no-op: the
        gateway still marks the operation terminal ``cancelled`` server-side
        and releases the budget reservation — the provider call is purely to
        stop paid work early where FAL's queue supports it.
        """
        if modality not in self.SUPPORTED_MODALITIES:
            raise ValueError(f"Unsupported FAL media modality: {modality}")
        if not self.api_key:
            raise ValueError("FAL_API_KEY is required when FAL_SOURCE=enabled")
        try:
            await self._call_blocking_with_timeout(
                self._cancel, self.model, operation_id
            )
            return True
        except Exception:  # noqa: BLE001 — best-effort by contract
            return False

    def _cancel(self, model: str, operation_id: str) -> Any:
        import fal_client  # type: ignore[import-not-found]

        # Older fal-client releases don't expose queue cancel — treat that as
        # an undeliverable cancel (caller degrades to server-side-only).
        cancel_fn = getattr(fal_client, "cancel", None)
        if cancel_fn is None:
            raise RuntimeError("fal_client.cancel is unavailable in this SDK version")
        return self._sdk_call(fal_client, "cancel", model, operation_id)

    def _result(self, model: str, operation_id: str) -> Dict[str, Any]:
        import fal_client  # type: ignore[import-not-found]

        return self._sdk_call(fal_client, "result", model, operation_id)

    def _sdk_call(self, module, method: str, *args, **kwargs):
        client_type = getattr(module, "SyncClient", None)
        if client_type is not None:
            client = client_type(
                key=self.api_key,
                default_timeout=float(self.timeout_seconds),
            )
            return getattr(client, method)(*args, **kwargs)
        return self._call_with_fal_key(
            getattr(module, method), *args, **kwargs
        )

    def _call_with_fal_key(self, func, *args, **kwargs):
        with _FAL_ENV_LOCK:
            previous = os.environ.get("FAL_KEY")
            os.environ["FAL_KEY"] = self.api_key
            try:
                return func(*args, **kwargs)
            finally:
                if previous is None:
                    os.environ.pop("FAL_KEY", None)
                else:
                    os.environ["FAL_KEY"] = previous

    async def _call_blocking_with_timeout(self, func, *args):
        return await asyncio.wait_for(
            asyncio.to_thread(func, *args), timeout=self.timeout_seconds
        )

    def _operation_payload(
        self,
        *,
        operation_id: str,
        status: str,
        model: str,
        modality: str,
        raw: Dict[str, Any],
    ) -> Dict[str, Any]:
        license_value, cost_usd = self._license_and_cost(model, modality)
        provenance: Dict[str, Any] = {
            "provider_request_id": operation_id,
            "modality": modality,
        }
        if modality == "image_to_3d":
            # image→3D endpoints do not report a settled cost in the result, so
            # the normalized cost is the registry estimate — flag the basis.
            provenance["cost_basis"] = "estimated"
        return {
            "operation_id": operation_id,
            "status": status,
            "provider": "fal",
            "model": model,
            "modality": modality,
            "artifact_url": None,
            "artifacts": [],
            "cost_usd": cost_usd,
            "license": license_value,
            "provenance": provenance,
            "raw": raw,
        }

    def _license_and_cost(
        self, model: str, modality: str
    ) -> Tuple[str, Optional[float]]:
        if modality == "image_to_3d":
            entry = media_registry.lookup(model)
            if entry is not None:
                return entry.license, entry.estimated_cost_usd
        return self.license, None

    def _extract_request_id(self, payload: Any) -> str:
        if isinstance(payload, dict):
            value = payload.get("request_id") or payload.get("requestId") or payload.get("id")
        else:
            value = (
                getattr(payload, "request_id", None)
                or getattr(payload, "requestId", None)
                or getattr(payload, "id", None)
            )
        return str(value or f"fal-{uuid.uuid4()}")

    def _normalize_status(self, payload: Any) -> str:
        # fal-client's queue `status()` returns type-discriminated dataclasses
        # (`Queued` / `InProgress` / `Completed`) whose *class name* signals
        # state and which carry no `.status`/`.state` attribute; a failed job is
        # a `Completed` carrying a truthy `error`/`error_type`. We fall back to
        # dict / string shapes for forward-compat and provider-client stubs.
        error: Any = None
        if isinstance(payload, dict):
            raw = payload.get("status") or payload.get("state")
            error = payload.get("error") or payload.get("error_type")
        else:
            raw = getattr(payload, "status", None) or getattr(payload, "state", None)
            error = getattr(payload, "error", None) or getattr(payload, "error_type", None)
            if raw is None:
                # Real fal Status objects signal state by class name only.
                raw = type(payload).__name__
        status_value = str(raw or "running").strip().lower()
        if status_value in {"completed", "complete", "succeeded", "success"}:
            # A completed operation carrying an error is a failure.
            return "failed" if error else "succeeded"
        if status_value in {"failed", "error"}:
            return "failed"
        if status_value in {"cancelled", "canceled"}:
            return "cancelled"
        if status_value in {"in_queue", "queued", "submitted"}:
            return "submitted"
        if status_value in {"in_progress", "inprogress", "running"}:
            return "running"
        return "running"

    def _extract_artifacts(self, payload: Dict[str, Any]) -> list[Dict[str, Any]]:
        artifacts: list[Dict[str, Any]] = []
        for image in payload.get("images", []) or []:
            if not isinstance(image, dict) or not image.get("url"):
                continue
            artifacts.append(
                {
                    "url": image["url"],
                    "content_type": image.get("content_type") or image.get("mime_type"),
                    "width": image.get("width"),
                    "height": image.get("height"),
                }
            )
        return artifacts

    def _extract_glb_artifacts(
        self, payload: Dict[str, Any], model: str
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Normalize an image→3D result into artifacts + leftover fields.

        Probes the per-model GLB / preview / texture response keys (ports
        DayDreams' ``extractGlbUrl``) and returns the GLB first so the gateway
        surfaces it as the normalized ``artifact_url``. Response keys that were
        not recognized are returned separately so the caller can preserve them
        under a namespaced provenance bag.
        """

        entry = media_registry.lookup(model)
        glb_keys = entry.glb_keys if entry else media_registry.GLB_RESPONSE_KEYS
        preview_keys = (
            entry.preview_keys if entry else media_registry.PREVIEW_RESPONSE_KEYS
        )
        texture_keys = (
            entry.texture_keys if entry else media_registry.TEXTURE_RESPONSE_KEYS
        )

        artifacts: List[Dict[str, Any]] = []
        consumed: set[str] = set()

        glb = self._probe_url(payload, glb_keys, consumed)
        if glb:
            artifacts.append(
                {
                    "url": glb["url"],
                    "content_type": glb.get("content_type") or "model/gltf-binary",
                    "role": "model_glb",
                    "source_key": glb["key"],
                }
            )
        preview = self._probe_url(payload, preview_keys, consumed)
        if preview:
            artifacts.append(
                {
                    "url": preview["url"],
                    "content_type": preview.get("content_type") or "image/png",
                    "role": "preview",
                    "source_key": preview["key"],
                }
            )
        for texture in self._probe_url_list(payload, texture_keys, consumed):
            artifacts.append(
                {
                    "url": texture["url"],
                    "content_type": texture.get("content_type"),
                    "role": "texture",
                    "source_key": texture["key"],
                }
            )

        provider_fields = {
            key: value for key, value in payload.items() if key not in consumed
        }
        return artifacts, provider_fields

    @staticmethod
    def _coerce_url(value: Any) -> Optional[Dict[str, Any]]:
        # A response key may hold a bare URL string or an object with a `url`.
        if isinstance(value, str) and value.strip():
            return {"url": value.strip(), "content_type": None}
        if isinstance(value, dict) and value.get("url"):
            return {
                "url": value["url"],
                "content_type": value.get("content_type") or value.get("mime_type"),
            }
        return None

    def _probe_url(
        self, payload: Dict[str, Any], keys: Tuple[str, ...], consumed: set
    ) -> Optional[Dict[str, Any]]:
        found: Optional[Dict[str, Any]] = None
        for key in keys:
            if key not in payload:
                continue
            coerced = self._coerce_url(payload[key])
            if coerced is None:
                # A recognized key holding a non-URL value (e.g. vendor
                # metadata) is left for provider_fields, not silently dropped.
                continue
            consumed.add(key)
            if found is None:
                coerced["key"] = key
                found = coerced
        return found

    def _probe_url_list(
        self, payload: Dict[str, Any], keys: Tuple[str, ...], consumed: set
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for key in keys:
            if key not in payload:
                continue
            value = payload[key]
            items = value if isinstance(value, list) else [value]
            coerced_any = False
            for item in items:
                coerced = self._coerce_url(item)
                if coerced:
                    coerced["key"] = key
                    out.append(coerced)
                    coerced_any = True
            if coerced_any:
                consumed.add(key)
        return out

    def _object_to_dict(self, payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if payload is None:
            return {}
        data = getattr(payload, "__dict__", None)
        if isinstance(data, dict):
            return dict(data)
        return {"value": str(payload)}
