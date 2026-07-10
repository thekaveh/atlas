from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Dict, Optional


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


class FalClient:
    """Small async wrapper around the blocking fal-client SDK."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        output_format: Optional[str] = None,
        enable_safety_checker: Optional[bool] = None,
    ) -> None:
        self.api_key = (api_key or os.getenv("FAL_API_KEY") or os.getenv("FAL_KEY") or "").strip()
        self.model = (model or os.getenv("FAL_MODEL") or "fal-ai/flux/dev").strip()
        self.output_format = (output_format or os.getenv("FAL_OUTPUT_FORMAT") or "jpeg").strip()
        try:
            self.timeout_seconds = int(os.getenv("FAL_TIMEOUT_SECONDS", "120") or "120")
        except ValueError:
            self.timeout_seconds = 120
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

        result = await asyncio.wait_for(
            asyncio.to_thread(self._subscribe, arguments),
            timeout=self.timeout_seconds,
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

        return self._call_with_fal_key(fal_client.subscribe, self.model, arguments=arguments)

    async def submit_media_operation(
        self,
        *,
        modality: str,
        input: Dict[str, Any],
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        if modality != "image":
            raise ValueError(f"Unsupported FAL media modality: {modality}")
        if not self.api_key:
            raise ValueError("FAL_API_KEY is required when FAL_SOURCE=enabled")

        selected_model = (model or self.model).strip()
        arguments = self._image_arguments(input)
        submitted = await asyncio.to_thread(self._submit, selected_model, arguments)
        operation_id = self._extract_request_id(submitted)

        return self._operation_payload(
            operation_id=operation_id,
            status="submitted",
            model=selected_model,
            modality=modality,
            raw=self._object_to_dict(submitted),
        )

    async def get_media_operation(self, *, operation_id: str, modality: str) -> Dict[str, Any]:
        if modality != "image":
            raise ValueError(f"Unsupported FAL media modality: {modality}")
        if not self.api_key:
            raise ValueError("FAL_API_KEY is required when FAL_SOURCE=enabled")

        status_payload = await asyncio.to_thread(self._status, self.model, operation_id)
        normalized_status = self._normalize_status(status_payload)
        result_payload: Dict[str, Any] = {}
        if normalized_status == "succeeded":
            result_payload = await asyncio.to_thread(self._result, self.model, operation_id)

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
            artifacts = self._extract_artifacts(result_payload)
            payload["artifacts"] = artifacts
            payload["artifact_url"] = artifacts[0]["url"] if artifacts else None
            payload["raw"] = result_payload
        return payload

    def _image_arguments(self, input_payload: Dict[str, Any]) -> Dict[str, Any]:
        width = int(input_payload.get("width") or 512)
        height = int(input_payload.get("height") or 512)
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
        return arguments

    def _submit(self, model: str, arguments: Dict[str, Any]) -> Any:
        import fal_client  # type: ignore[import-not-found]

        return self._call_with_fal_key(fal_client.submit, model, arguments=arguments)

    def _status(self, model: str, operation_id: str) -> Any:
        import fal_client  # type: ignore[import-not-found]

        return self._call_with_fal_key(fal_client.status, model, operation_id)

    def _result(self, model: str, operation_id: str) -> Dict[str, Any]:
        import fal_client  # type: ignore[import-not-found]

        return self._call_with_fal_key(fal_client.result, model, operation_id)

    def _call_with_fal_key(self, func, *args, **kwargs):
        previous = os.environ.get("FAL_KEY")
        os.environ["FAL_KEY"] = self.api_key
        try:
            return func(*args, **kwargs)
        finally:
            if previous is None:
                os.environ.pop("FAL_KEY", None)
            else:
                os.environ["FAL_KEY"] = previous

    def _operation_payload(
        self,
        *,
        operation_id: str,
        status: str,
        model: str,
        modality: str,
        raw: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "operation_id": operation_id,
            "status": status,
            "provider": "fal",
            "model": model,
            "modality": modality,
            "artifact_url": None,
            "artifacts": [],
            "cost_usd": None,
            "license": self.license,
            "provenance": {"provider_request_id": operation_id},
            "raw": raw,
        }

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
        if isinstance(payload, dict):
            raw = payload.get("status") or payload.get("state")
        else:
            raw = getattr(payload, "status", None) or getattr(payload, "state", None)
        status_value = str(raw or "running").strip().lower()
        if status_value in {"completed", "complete", "succeeded", "success"}:
            return "succeeded"
        if status_value in {"failed", "error"}:
            return "failed"
        if status_value in {"cancelled", "canceled"}:
            return "cancelled"
        if status_value in {"in_queue", "queued", "submitted"}:
            return "submitted"
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

    def _object_to_dict(self, payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if payload is None:
            return {}
        data = getattr(payload, "__dict__", None)
        if isinstance(data, dict):
            return dict(data)
        return {"value": str(payload)}
