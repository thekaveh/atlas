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
        os.environ["FAL_KEY"] = self.api_key
        import fal_client  # type: ignore[import-not-found]

        return fal_client.subscribe(self.model, arguments=arguments)
