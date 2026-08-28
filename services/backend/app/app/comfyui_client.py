"""
ComfyUI client for interfacing with ComfyUI API
"""
import httpx
import asyncio
import time
import uuid
from typing import Dict, Any, Optional, List
import os
import logging
from urllib.parse import quote

logger = logging.getLogger(__name__)


class ComfyUIHistoryUnavailableError(RuntimeError):
    """Raised when ComfyUI history cannot be read."""


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
        try:
            response = await self.client.get(f"{self.base_url}/object_info")
            response.raise_for_status()
            object_info = response.json()
            
            models = {}
            
            # Extract checkpoint models
            if "CheckpointLoaderSimple" in object_info:
                checkpoint_info = object_info["CheckpointLoaderSimple"]["input"]["required"]
                if "ckpt_name" in checkpoint_info:
                    models["checkpoints"] = checkpoint_info["ckpt_name"][0]
            
            # Extract VAE models
            if "VAELoader" in object_info:
                vae_info = object_info["VAELoader"]["input"]["required"]
                if "vae_name" in vae_info:
                    models["vae"] = vae_info["vae_name"][0]
            
            # Extract ControlNet models
            if "ControlNetLoader" in object_info:
                controlnet_info = object_info["ControlNetLoader"]["input"]["required"]
                if "control_net_name" in controlnet_info:
                    models["controlnet"] = controlnet_info["control_net_name"][0]
            
            # Extract LoRA models
            if "LoraLoader" in object_info:
                lora_info = object_info["LoraLoader"]["input"]["required"]
                if "lora_name" in lora_info:
                    models["lora"] = lora_info["lora_name"][0]
            
            return models
            
        except Exception as exc:
            logger.error("Failed to get ComfyUI models (error_type=%s)", type(exc).__name__)
            return {}
    
    async def queue_prompt(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """Queue a workflow for execution"""
        try:
            # Generate a unique client_id for this request
            client_id = str(uuid.uuid4())
            
            prompt_data = {
                "prompt": workflow,
                "client_id": client_id
            }
            
            response = await self.client.post(
                f"{self.base_url}/prompt",
                json=prompt_data
            )
            response.raise_for_status()
            result = response.json()
            
            return {
                "success": True,
                "prompt_id": result.get("prompt_id"),
                "client_id": client_id,
                "number": result.get("number")
            }
            
        except Exception as exc:
            logger.error("Failed to queue prompt (error_type=%s)", type(exc).__name__)
            return {
                "success": False,
                "error": "ComfyUI prompt submission failed"
            }
    
    async def get_history(self, prompt_id: Optional[str] = None) -> Dict[str, Any]:
        """Get execution history"""
        try:
            url = f"{self.base_url}/history"
            if prompt_id:
                url += f"/{prompt_id}"
            
            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()
            
        except Exception as exc:
            logger.error("Failed to get history (error_type=%s)", type(exc).__name__)
            raise ComfyUIHistoryUnavailableError(
                "ComfyUI history is unavailable"
            ) from exc
    
    async def get_queue_status(self) -> Dict[str, Any]:
        """Get current queue status"""
        try:
            response = await self.client.get(f"{self.base_url}/queue")
            response.raise_for_status()
            return response.json()
            
        except Exception as exc:
            logger.error("Failed to get queue status (error_type=%s)", type(exc).__name__)
            return {}
    
    async def cancel_prompt(self, prompt_id: str) -> bool:
        """Request atomic cancellation of exactly one ComfyUI job."""
        try:
            job_id = quote(prompt_id, safe="")
            response = await self.client.post(
                f"{self.base_url}/api/jobs/{job_id}/cancel"
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or set(body) != {"cancelled"}:
                return False
            cancelled = body["cancelled"]
            return cancelled if type(cancelled) is bool else False
            
        except Exception as exc:
            logger.error("Failed to cancel prompt (error_type=%s)", type(exc).__name__)
            return False
    
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
                return {
                    "success": False,
                    "error": "Timeout waiting for completion"
                }
            
            try:
                history = await asyncio.wait_for(
                    self.get_history(prompt_id), timeout=remaining
                )
            except asyncio.TimeoutError:
                return {
                    "success": False,
                    "error": "Timeout waiting for completion"
                }
            except ComfyUIHistoryUnavailableError:
                return {
                    "success": False,
                    "error": "ComfyUI history is unavailable",
                    "prompt_id": prompt_id,
                }
            
            if prompt_id in history:
                prompt_history = history[prompt_id]
                
                # Check if completed
                if "outputs" in prompt_history:
                    return {
                        "success": True,
                        "outputs": prompt_history["outputs"],
                        "status": prompt_history.get("status", {}),
                        "prompt_id": prompt_id
                    }
                
                # Check if failed
                if "status" in prompt_history and prompt_history["status"].get("status_str") == "error":
                    return {
                        "success": False,
                        "error": "ComfyUI generation failed",
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
                if declared and int(declared) > self.max_image_bytes:
                    raise ValueError("ComfyUI image exceeds configured byte limit")
                async for chunk in response.aiter_bytes():
                    if len(chunks) + len(chunk) > self.max_image_bytes:
                        raise ValueError("ComfyUI image exceeds configured byte limit")
                    chunks.extend(chunk)
            return bytes(chunks)
            
        except Exception as exc:
            logger.error("Failed to get image data (error_type=%s)", type(exc).__name__)
            raise
