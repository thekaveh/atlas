from __future__ import annotations

import asyncio
import inspect
import math
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx

import media_registry


class FalSubmissionAmbiguousError(asyncio.TimeoutError):
    """Atlas timed out before FAL returned the accepted request identifier."""


_DEFAULT_IMAGE_MODEL = "fal-ai/flux/dev"
_DEFAULT_IMAGE_TO_IMAGE_MODEL = "fal-ai/flux/dev/image-to-image"
_FAL_OUTPUT_FORMATS = {"jpeg", "png"}
_FAL_TIMEOUT_MAX_SECONDS = 3600.0


def _first_not_none(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def validate_image_prompt(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError(
            "FAL image prompt must be a non-empty string of at most 4000 characters"
        )
    return value


def validate_image_request_shape(input_payload: Dict[str, Any]) -> str:
    """Validate schema fields shared by the route and provider boundary."""
    prompt = validate_image_prompt(input_payload.get("prompt"))
    image_size = input_payload.get("image_size")
    if image_size is not None and not isinstance(image_size, dict):
        raise ValueError("FAL image image_size must be an object")
    seed = input_payload.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError("FAL image seed must be an integer")
    return prompt


def _image_init_value(input_payload: Dict[str, Any]) -> Optional[str]:
    for key in ("image_url", "image", "init_image"):
        value = input_payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"FAL image {key} must be a non-empty string")
        return value.strip()
    return None


def _bounded_int(name: str, value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"FAL image {name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(
            f"FAL image {name} must be between {minimum} and {maximum}"
        )
    return value


def _bounded_float(name: str, value: Any, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"FAL image {name} must be a number")
    try:
        converted = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"FAL image {name} must be a number") from exc
    if not math.isfinite(converted) or not minimum <= converted <= maximum:
        raise ValueError(
            f"FAL image {name} must be a finite number between "
            f"{minimum:g} and {maximum:g}"
        )
    return converted


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return True
    if raw in {"0", "false", "no", "off", "disabled"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _timeout_seconds(value: Any) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("FAL timeout must be finite") from exc
    if not math.isfinite(converted):
        raise ValueError("FAL timeout must be finite")
    if converted <= 0:
        raise ValueError("FAL timeout must be greater than 0")
    if converted > _FAL_TIMEOUT_MAX_SECONDS:
        raise ValueError("FAL timeout must be at most 3600 seconds")
    return converted


def fal_timeout_seconds_from_env() -> float:
    return _timeout_seconds(os.getenv("FAL_TIMEOUT_SECONDS", "120") or "120")


def _output_format(value: Any) -> str:
    converted = str(value).strip().lower()
    if converted not in _FAL_OUTPUT_FORMATS:
        raise ValueError("FAL output format must be jpeg or png")
    return converted


def validate_fal_config() -> None:
    """Fail startup before a malformed provider setting reaches paid work."""
    fal_timeout_seconds_from_env()
    _output_format(os.getenv("FAL_OUTPUT_FORMAT", "jpeg") or "jpeg")
    _env_bool("FAL_ENABLE_SAFETY_CHECKER", True)


class FalClient:
    """Small async wrapper around fal-client's cancellable async SDK."""

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
        self.output_format = _output_format(
            output_format
            if output_format is not None
            else os.getenv("FAL_OUTPUT_FORMAT", "jpeg") or "jpeg"
        )
        self.timeout_seconds = (
            fal_timeout_seconds_from_env()
            if timeout_seconds is None
            else _timeout_seconds(timeout_seconds)
        )
        if enable_safety_checker is None:
            self.enable_safety_checker = _env_bool(
                "FAL_ENABLE_SAFETY_CHECKER", True
            )
        elif isinstance(enable_safety_checker, bool):
            self.enable_safety_checker = enable_safety_checker
        else:
            raise ValueError("FAL enable_safety_checker must be a boolean")
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

        arguments = self._image_arguments(
            {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "width": width,
                "height": height,
                "steps": steps,
                "cfg": cfg,
                "seed": seed,
            },
            selected_model=self.model,
            init_image=None,
        )

        result = await self._call_async_with_timeout(
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

    async def _subscribe(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        import fal_client  # type: ignore[import-not-found]

        return await self._sdk_call(
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

        selected_model, arguments = self._prepare_media_operation(
            modality=modality, input=input, model=model
        )
        try:
            submitted = await self._call_async_with_timeout(
                self._submit, selected_model, arguments
            )
        except asyncio.TimeoutError as exc:
            raise FalSubmissionAmbiguousError(
                "FAL submission timed out before a provider request id was returned; "
                "the provider may still have accepted the request"
            ) from exc
        except (httpx.TransportError, ConnectionError, OSError) as exc:
            raise FalSubmissionAmbiguousError(
                "FAL submission transport failed before a provider request id was "
                "returned; the provider may still have accepted the request"
            ) from exc
        operation_id = self._extract_request_id(submitted)

        return self._operation_payload(
            operation_id=operation_id,
            status="submitted",
            model=selected_model,
            modality=modality,
            raw=self._object_to_dict(submitted),
        )

    def preflight_media_operation(
        self,
        *,
        modality: str,
        input: Dict[str, Any],
        model: Optional[str] = None,
    ) -> str:
        """Validate a request without provider, storage, or accounting work."""
        selected_model, _ = self._prepare_media_operation(
            modality=modality, input=input, model=model
        )
        return selected_model

    def _prepare_media_operation(
        self,
        *,
        modality: str,
        input: Dict[str, Any],
        model: Optional[str],
    ) -> Tuple[str, Dict[str, Any]]:
        if modality not in self.SUPPORTED_MODALITIES:
            raise ValueError(f"Unsupported FAL media modality: {modality}")
        selected_model = (model or self.model).strip()
        if modality == "image_to_3d":
            entry = media_registry.lookup(selected_model)
            if entry is None:
                raise ValueError(f"Unknown FAL image_to_3d endpoint: {selected_model}")
            if not entry.endpoint_verified:
                raise ValueError(
                    f"FAL image_to_3d endpoint {entry.model_id} is not verified"
                )
            return entry.model_id, self._image_to_3d_arguments(input, entry.family)
        init_image = _image_init_value(input)
        if init_image is not None and selected_model == _DEFAULT_IMAGE_MODEL:
            selected_model = _DEFAULT_IMAGE_TO_IMAGE_MODEL
        arguments = self._image_arguments(
            input,
            selected_model=selected_model,
            init_image=init_image,
        )
        return selected_model, arguments

    async def get_media_operation(self, *, operation_id: str, modality: str) -> Dict[str, Any]:
        if modality not in self.SUPPORTED_MODALITIES:
            raise ValueError(f"Unsupported FAL media modality: {modality}")
        if not self.api_key:
            raise ValueError("FAL_API_KEY is required when FAL_SOURCE=enabled")

        status_payload = await self._call_async_with_timeout(
            self._status, self.model, operation_id
        )
        normalized_status = self._normalize_status(status_payload)
        result_payload: Dict[str, Any] = {}
        if normalized_status == "succeeded":
            result_payload = await self._call_async_with_timeout(
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

    def _image_arguments(
        self,
        input_payload: Dict[str, Any],
        *,
        selected_model: str,
        init_image: Optional[str],
    ) -> Dict[str, Any]:
        prompt = validate_image_request_shape(input_payload)
        if selected_model not in {
            _DEFAULT_IMAGE_MODEL,
            _DEFAULT_IMAGE_TO_IMAGE_MODEL,
        }:
            provider_arguments = input_payload.get("provider_arguments")
            if not isinstance(provider_arguments, dict) or not provider_arguments:
                raise ValueError(
                    "Custom FAL image endpoints require non-empty "
                    "input.provider_arguments matching the provider schema"
                )
            if provider_arguments.get("prompt") != prompt:
                raise ValueError(
                    "Custom FAL provider_arguments must contain a matching prompt"
                )
            return dict(provider_arguments)

        if input_payload.get("provider_arguments") is not None:
            raise ValueError(
                "FAL provider_arguments are only accepted for custom image endpoints"
            )
        if input_payload.get("negative_prompt"):
            raise ValueError(
                f"FAL image negative_prompt is not supported by {selected_model}"
            )

        is_image_to_image = selected_model == _DEFAULT_IMAGE_TO_IMAGE_MODEL
        if is_image_to_image and init_image is None:
            raise ValueError(
                f"FAL image endpoint {selected_model} requires an init image"
            )
        if is_image_to_image:
            unsupported = next(
                (
                    key
                    for key in ("width", "height", "image_size")
                    if input_payload.get(key) is not None
                ),
                None,
            )
            if unsupported is not None:
                raise ValueError(
                    f"FAL image {unsupported} is not supported by {selected_model}"
                )
            min_steps, max_steps, min_cfg, max_cfg = 10, 50, 1, 20
        else:
            min_steps, max_steps, min_cfg, max_cfg = 1, 50, 1, 20
        steps = _bounded_int(
            "steps",
            _first_not_none(input_payload.get("steps"), 20),
            min_steps,
            max_steps,
        )
        cfg = _bounded_float(
            "cfg",
            _first_not_none(
                input_payload.get("cfg"), input_payload.get("guidance_scale"), 7.0
            ),
            min_cfg,
            max_cfg,
        )
        num_images = _bounded_int(
            "num_images",
            _first_not_none(input_payload.get("num_images"), 1),
            1,
            4,
        )
        arguments: Dict[str, Any] = {
            "prompt": prompt,
            "num_inference_steps": steps,
            "guidance_scale": cfg,
            "num_images": num_images,
            "enable_safety_checker": self.enable_safety_checker,
            "output_format": self.output_format,
        }
        if not is_image_to_image:
            # Only the default text endpoint defines image_size. Flat keys win;
            # the nested object is retained as a compatibility fallback.
            nested_size = input_payload.get("image_size")
            if not isinstance(nested_size, dict):
                nested_size = {}
            width = _bounded_int(
                "width",
                _first_not_none(
                    input_payload.get("width"), nested_size.get("width"), 512
                ),
                64,
                4096,
            )
            height = _bounded_int(
                "height",
                _first_not_none(
                    input_payload.get("height"), nested_size.get("height"), 512
                ),
                64,
                4096,
            )
            arguments["image_size"] = {"width": width, "height": height}
        if input_payload.get("seed") is not None:
            arguments["seed"] = input_payload["seed"]
        if init_image is not None:
            arguments["image_url"] = init_image
            if input_payload.get("strength") is not None:
                arguments["strength"] = _bounded_float(
                    "strength", input_payload["strength"], 0.01, 1
                )
        elif input_payload.get("strength") is not None:
            raise ValueError("FAL image strength requires an init image")
        return arguments

    def _image_to_3d_arguments(
        self, input_payload: Dict[str, Any], family: str
    ) -> Dict[str, Any]:
        image = input_payload.get("image")
        if not isinstance(image, str) or not image.strip():
            raise ValueError(
                "image_to_3d input requires a non-empty 'image' (URL or data URI)"
            )
        unsupported = sorted(set(input_payload) - {"image", "seed"})
        if unsupported:
            raise ValueError(
                "FAL image_to_3d input contains unsupported fields: "
                + ", ".join(unsupported)
            )

        image_fields: Dict[str, str] = {
            "trellis": "image_url",
            "hunyuan3d": "input_image_url",
            "tripo": "image_url",
            "rodin": "input_image_urls",
        }
        image_field = image_fields.get(family)
        if image_field is None:
            raise ValueError(f"Unsupported FAL image_to_3d family: {family}")
        image_value: Any = [image.strip()] if family == "rodin" else image.strip()
        arguments: Dict[str, Any] = {image_field: image_value}
        if input_payload.get("seed") is not None:
            seed_max = 65535 if family == "rodin" else 2_147_483_647
            arguments["seed"] = _bounded_int(
                "seed", input_payload["seed"], 0, seed_max
            )
        return arguments

    async def _submit(self, model: str, arguments: Dict[str, Any]) -> Any:
        import fal_client  # type: ignore[import-not-found]

        return await self._sdk_call(
            fal_client, "submit", model, arguments=arguments
        )

    async def _status(self, model: str, operation_id: str) -> Any:
        import fal_client  # type: ignore[import-not-found]

        return await self._sdk_call(fal_client, "status", model, operation_id)

    async def cancel_media_operation(self, *, operation_id: str, modality: str) -> bool:
        """Best-effort provider-side cancel of an in-flight operation (#518).

        Returns True when the provider accepted the cancel, False when the
        cancel could not be delivered (SDK without ``cancel``, network error,
        already-settled request, …). Acceptance is not a terminal outcome;
        callers retain accounting state until a later poll confirms completion.
        """
        if modality not in self.SUPPORTED_MODALITIES:
            raise ValueError(f"Unsupported FAL media modality: {modality}")
        if not self.api_key:
            raise ValueError("FAL_API_KEY is required when FAL_SOURCE=enabled")
        try:
            await self._call_async_with_timeout(
                self._cancel, self.model, operation_id
            )
            return True
        except Exception:  # noqa: BLE001 — best-effort by contract
            return False

    async def _cancel(self, model: str, operation_id: str) -> Any:
        import fal_client  # type: ignore[import-not-found]

        return await self._sdk_call(fal_client, "cancel", model, operation_id)

    async def _result(self, model: str, operation_id: str) -> Dict[str, Any]:
        import fal_client  # type: ignore[import-not-found]

        return await self._sdk_call(fal_client, "result", model, operation_id)

    async def _sdk_call(self, module, method: str, *args, **kwargs):
        client_type = getattr(module, "AsyncClient", None)
        if client_type is not None:
            client = client_type(
                key=self.api_key,
                default_timeout=float(self.timeout_seconds),
            )
            if method == "submit":
                kwargs.setdefault("start_timeout", self.timeout_seconds)
            elif method == "subscribe":
                native_timeout = max(0.001, self.timeout_seconds * 0.9)
                kwargs.setdefault("start_timeout", native_timeout)
                kwargs.setdefault("client_timeout", native_timeout)
            return await getattr(client, method)(*args, **kwargs)

        # Narrow test seam for async stubs. Shipped environments require
        # fal-client>=1.0.0 and therefore always use AsyncClient.
        function = getattr(module, method, None)
        if function is None or not inspect.iscoroutinefunction(function):
            raise RuntimeError(
                "fal-client>=1.0.0 with AsyncClient support is required"
            )
        return await function(*args, **kwargs)

    async def _call_async_with_timeout(self, func, *args):
        result = func(*args)
        if not inspect.isawaitable(result):
            raise RuntimeError("FAL SDK operation did not return an awaitable")
        return await asyncio.wait_for(result, timeout=self.timeout_seconds)

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
        if not value:
            raise FalSubmissionAmbiguousError(
                "FAL submission response did not include a provider request ID"
            )
        return str(value)

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


def preflight_media_operation(
    *, modality: str, input: Dict[str, Any], model: Optional[str] = None
) -> str:
    """Validate and normalize media input without invoking provider work."""
    return FalClient(api_key="preflight", model=model).preflight_media_operation(
        modality=modality,
        input=input,
        model=model,
    )
