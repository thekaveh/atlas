"""
ComfyUI client for interfacing with ComfyUI API
"""
import httpx
import asyncio
import time
import uuid
from typing import Awaitable, Dict, Any, Optional, List
import os
import logging
from collections.abc import Mapping
from urllib.parse import quote

logger = logging.getLogger(__name__)


class ComfyUIUpstreamError(RuntimeError):
    """Base class for safe, typed ComfyUI upstream failures."""


class ComfyUIUnavailableError(ComfyUIUpstreamError):
    """Raised when no usable HTTP response arrives from ComfyUI."""


class ComfyUIResponseError(ComfyUIUpstreamError):
    """Raised when ComfyUI returns a failed or malformed HTTP response."""


class ComfyUIHistoryUnavailableError(ComfyUIUnavailableError):
    """Raised when ComfyUI history cannot be read."""


class ComfyUIImageTooLargeError(ValueError):
    """Raised when a valid image response exceeds Atlas's local byte cap."""


def _response_json(response: httpx.Response) -> Any:
    """Return JSON only from a successful, decodable ComfyUI response."""
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ComfyUIResponseError(
            "ComfyUI returned an invalid response"
        ) from exc
    try:
        return response.json()
    except (ValueError, UnicodeError) as exc:
        raise ComfyUIResponseError(
            "ComfyUI returned an invalid response"
        ) from exc


def _log_upstream_failure(operation: str, exc: Exception) -> None:
    logger.error("%s (error_type=%s)", operation, type(exc).__name__)


async def _request_json(
    request: Awaitable[httpx.Response],
    *,
    operation: str,
    unavailable_error: type[ComfyUIUnavailableError] = ComfyUIUnavailableError,
) -> Any:
    """Execute one JSON request while preserving failure category."""
    try:
        response = await request
    except httpx.TransportError as exc:
        _log_upstream_failure(operation, exc)
        message = (
            "ComfyUI history is unavailable"
            if unavailable_error is ComfyUIHistoryUnavailableError
            else "ComfyUI is unavailable"
        )
        raise unavailable_error(message) from exc
    return _response_json(response)


def _target_history(
    history: Dict[str, Any], prompt_id: str
) -> Optional[Mapping[str, Any]]:
    """Validate the requested ComfyUI history record before interpreting it."""
    if prompt_id not in history:
        return None
    target = history[prompt_id]
    if not isinstance(target, Mapping):
        raise ComfyUIResponseError("ComfyUI returned an invalid response")

    if "outputs" in target and not isinstance(target["outputs"], Mapping):
        raise ComfyUIResponseError("ComfyUI returned an invalid response")

    if "status" in target:
        status_value = target["status"]
        if not isinstance(status_value, Mapping):
            raise ComfyUIResponseError("ComfyUI returned an invalid response")
        if "status_str" in status_value and not isinstance(
            status_value["status_str"], str
        ):
            raise ComfyUIResponseError("ComfyUI returned an invalid response")
    return target


class ComfyUIClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or os.getenv("COMFYUI_BASE_URL", "http://comfyui:18188")
        self.base_url = self.base_url.rstrip('/')
        self.max_image_bytes = int(os.getenv("COMFYUI_MAX_IMAGE_BYTES", "20971520"))
        if self.max_image_bytes <= 0:
            raise ValueError("COMFYUI_MAX_IMAGE_BYTES must be positive")
        # connect=5 fails fast on a down ComfyUI (single budget=60 would
        # wait the full minute before reporting unhealthy); read=60 keeps
        # the long budget for image-generation HTTP rounds that legitimately
        # take that long.
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)
        )
        
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()
    
    async def health_check(self) -> Dict[str, Any]:
        """Check if ComfyUI is available and responsive"""
        try:
            response = await self.client.get(f"{self.base_url}/system_stats")
            response.raise_for_status()
            return {
                "status": "healthy",
                "response_time": response.elapsed.total_seconds(),
                "system_stats": response.json()
            }
        except Exception as exc:
            logger.error("ComfyUI health check failed (error_type=%s)", type(exc).__name__)
            return {
                "status": "unhealthy",
                "error": "ComfyUI is unavailable"
            }
    
    async def get_models(self) -> Dict[str, List[str]]:
        """Get available models from ComfyUI"""
        object_info = await _request_json(
            self.client.get(f"{self.base_url}/object_info"),
            operation="Failed to get ComfyUI models",
        )
        if not isinstance(object_info, dict):
            raise ComfyUIResponseError("ComfyUI returned an invalid response")

        models: Dict[str, List[str]] = {}
        loaders = (
            ("CheckpointLoaderSimple", "ckpt_name", "checkpoints"),
            ("VAELoader", "vae_name", "vae"),
            ("ControlNetLoader", "control_net_name", "controlnet"),
            ("LoraLoader", "lora_name", "lora"),
        )
        for loader_name, input_name, result_name in loaders:
            if loader_name not in object_info:
                continue
            try:
                choices = object_info[loader_name]["input"]["required"][input_name][0]
            except (KeyError, IndexError, TypeError) as exc:
                raise ComfyUIResponseError(
                    "ComfyUI returned an invalid response"
                ) from exc
            if not isinstance(choices, list) or not all(
                isinstance(choice, str) for choice in choices
            ):
                raise ComfyUIResponseError(
                    "ComfyUI returned an invalid response"
                )
            models[result_name] = choices
        return models
    
    async def queue_prompt(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Queue a workflow for execution"""
        # Generate a unique client_id for this request
        client_id = str(uuid.uuid4())
        prompt_data = {
            "prompt": workflow,
            "client_id": client_id
        }
        result = await _request_json(
            self.client.post(
                f"{self.base_url}/prompt",
                json=prompt_data
            ),
            operation="Failed to queue ComfyUI prompt",
        )
        prompt_id = result.get("prompt_id") if isinstance(result, dict) else None
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            raise ComfyUIResponseError("ComfyUI returned an invalid response")
        return {
            "success": True,
            "prompt_id": prompt_id,
            "client_id": client_id,
            "number": result.get("number")
        }
    
    async def get_history(self, prompt_id: Optional[str] = None) -> Dict[str, Any]:
        """Get execution history"""
        url = f"{self.base_url}/history"
        if prompt_id is not None:
            url += f"/{prompt_id}"
        history = await _request_json(
            self.client.get(url),
            operation="Failed to get ComfyUI history",
            unavailable_error=ComfyUIHistoryUnavailableError,
        )
        if not isinstance(history, dict):
            raise ComfyUIResponseError("ComfyUI returned an invalid response")
        if prompt_id is not None:
            _target_history(history, prompt_id)
        return history
    
    async def get_queue_status(self) -> Dict[str, Any]:
        """Get current queue status"""
        queue = await _request_json(
            self.client.get(f"{self.base_url}/queue"),
            operation="Failed to get ComfyUI queue status",
        )
        if not isinstance(queue, dict):
            raise ComfyUIResponseError("ComfyUI returned an invalid response")
        return queue
    
    async def cancel_prompt(self, prompt_id: str) -> bool:
        """Request atomic cancellation of exactly one ComfyUI job."""
        job_id = quote(prompt_id, safe="")
        body = await _request_json(
            self.client.post(
                f"{self.base_url}/api/jobs/{job_id}/cancel"
            ),
            operation="Failed to cancel ComfyUI prompt",
        )
        if not isinstance(body, dict) or set(body) != {"cancelled"}:
            raise ComfyUIResponseError("ComfyUI returned an invalid response")
        cancelled = body["cancelled"]
        if type(cancelled) is not bool:
            raise ComfyUIResponseError("ComfyUI returned an invalid response")
        return cancelled
    
    async def generate_simple_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 512,
        height: int = 512,
        steps: int = 20,
        cfg: float = 7.0,
        seed: Optional[int] = None,
        checkpoint: str = "v1-5-pruned-emaonly.safetensors"
    ) -> Dict[str, Any]:
        """Generate an image using a simple text-to-image workflow"""
        
        # Generate random seed if not provided
        if seed is None:
            seed = int.from_bytes(os.urandom(4), byteorder='big') % (2**32)
        
        # Create a simple workflow
        workflow = {
            "1": {
                "inputs": {
                    "ckpt_name": checkpoint
                },
                "class_type": "CheckpointLoaderSimple"
            },
            "2": {
                "inputs": {
                    "text": prompt,
                    "clip": ["1", 1]
                },
                "class_type": "CLIPTextEncode"
            },
            "3": {
                "inputs": {
                    "text": negative_prompt,
                    "clip": ["1", 1]
                },
                "class_type": "CLIPTextEncode"
            },
            "4": {
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1.0,
                    "model": ["1", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["5", 0]
                },
                "class_type": "KSampler"
            },
            "5": {
                "inputs": {
                    "width": width,
                    "height": height,
                    "batch_size": 1
                },
                "class_type": "EmptyLatentImage"
            },
            "6": {
                "inputs": {
                    "samples": ["4", 0],
                    "vae": ["1", 2]
                },
                "class_type": "VAEDecode"
            },
            "7": {
                "inputs": {
                    "filename_prefix": "ComfyUI",
                    "images": ["6", 0]
                },
                "class_type": "SaveImage"
            }
        }
        
        # Queue the workflow
        result = await self.queue_prompt(workflow)
        
        if result.get("success"):
            return {
                "success": True,
                "prompt_id": result["prompt_id"],
                "client_id": result["client_id"],
                "parameters": {
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                    "width": width,
                    "height": height,
                    "steps": steps,
                    "cfg": cfg,
                    "seed": seed,
                    "checkpoint": checkpoint
                }
            }
        else:
            return result
    
    async def wait_for_completion(self, prompt_id: str, timeout: float) -> Dict[str, Any]:
        """Wait for a prompt to complete execution"""
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = time.monotonic() + timeout
        
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError

            history = await asyncio.wait_for(
                self.get_history(prompt_id), timeout=remaining
            )
            prompt_history = _target_history(history, prompt_id)
            if prompt_history is not None:
                status_value = prompt_history.get("status", {})

                # A failed prompt can still have partial outputs. Error wins.
                if status_value.get("status_str") == "error":
                    return {
                        "success": False,
                        "error": "ComfyUI generation failed",
                        "prompt_id": prompt_id
                    }

                if "outputs" in prompt_history:
                    return {
                        "success": True,
                        "outputs": prompt_history["outputs"],
                        "status": status_value,
                        "prompt_id": prompt_id
                    }
            
            await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))
    
    async def get_image_data(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        """Get image data from ComfyUI"""
        try:
            params = {
                "filename": filename,
                "type": folder_type
            }
            if subfolder:
                params["subfolder"] = subfolder
            
            chunks = bytearray()
            async with self.client.stream(
                "GET", f"{self.base_url}/view", params=params
            ) as response:
                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared is not None:
                    if not declared or not all(
                        "0" <= char <= "9" for char in declared
                    ):
                        raise ComfyUIResponseError(
                            "ComfyUI returned an invalid response"
                        )
                    try:
                        declared_bytes = int(declared)
                    except ValueError as exc:
                        raise ComfyUIResponseError(
                            "ComfyUI returned an invalid response"
                        ) from exc
                    if declared_bytes > self.max_image_bytes:
                        raise ComfyUIImageTooLargeError(
                            "ComfyUI image exceeds configured byte limit"
                        )
                async for chunk in response.aiter_bytes():
                    if len(chunks) + len(chunk) > self.max_image_bytes:
                        raise ComfyUIImageTooLargeError(
                            "ComfyUI image exceeds configured byte limit"
                        )
                    chunks.extend(chunk)
            return bytes(chunks)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise
            _log_upstream_failure("Failed to get ComfyUI image data", exc)
            raise ComfyUIResponseError(
                "ComfyUI returned an invalid response"
            ) from exc
        except httpx.TransportError as exc:
            _log_upstream_failure("Failed to get ComfyUI image data", exc)
            raise ComfyUIUnavailableError("ComfyUI is unavailable") from exc
        except (ComfyUIUpstreamError, ComfyUIImageTooLargeError):
            raise
        except Exception as exc:
            logger.error("Failed to get image data (error_type=%s)", type(exc).__name__)
            raise
