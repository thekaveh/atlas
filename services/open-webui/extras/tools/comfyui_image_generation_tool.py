"""
title: ComfyUI Image Generator
author: Atlas
author_url: https://github.com/thekaveh/atlas
description: AI-powered image generation using ComfyUI
required_open_webui_version: 0.4.4
requirements: requests
version: 1.0.0
license: MIT
"""

import os
import requests
from pydantic import BaseModel, Field
from typing import Optional, List


class Tools:
    class Valves(BaseModel):
        backend_url: str = Field(
            default="http://backend:8000", description="Backend API URL"
        )
        timeout: int = Field(
            default_factory=lambda: int(
                os.getenv("COMFYUI_COMPLETION_TIMEOUT_SECONDS", "300")
            ),
            ge=1,
            le=3600,
            description="End-to-end generation deadline in seconds",
        )
        enable_tool: bool = Field(
            default=True, description="Enable this image generation tool"
        )
        default_width: int = Field(default=512, description="Default image width")
        default_height: int = Field(default=512, description="Default image height")
        default_steps: int = Field(
            default=20, description="Default number of generation steps"
        )
        default_cfg: float = Field(
            default=7.0, description="Default CFG (classifier-free guidance) scale"
        )

    def __init__(self):
        self.valves = self.Valves()

    @staticmethod
    def _backend_headers() -> dict[str, str]:
        token = (os.getenv("BACKEND_OPEN_WEBUI_API_TOKEN") or "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def generate_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,
        checkpoint: str = "v1-5-pruned-emaonly.safetensors",
    ) -> str:
        """
        Generate an image using ComfyUI with the specified parameters.

        :param prompt: The text prompt describing the image to generate
        :param negative_prompt: Text describing what to avoid in the image (optional)
        :param width: Image width in pixels (default: 512)
        :param height: Image height in pixels (default: 512)
        :param steps: Number of denoising steps (default: 20)
        :param cfg: CFG scale for guidance strength (default: 7.0)
        :param checkpoint: Model checkpoint to use (default: v1-5-pruned-emaonly.safetensors)
        :return: Image generation result with an artifact filename or error message
        """

        if not self.valves.enable_tool:
            return "❌ Image generation tool is currently disabled."

        if not prompt:
            return "❌ Please provide a prompt for image generation."

        # Use provided values or defaults
        width = width or self.valves.default_width
        height = height or self.valves.default_height
        steps = steps or self.valves.default_steps
        cfg = cfg or self.valves.default_cfg

        try:
            # First check if ComfyUI service is healthy
            health_resp = requests.get(
                f"{self.valves.backend_url}/comfyui/health",
                headers=self._backend_headers(),
                timeout=10,
            )

            if health_resp.status_code != 200:
                return "❌ ComfyUI service is not available. Please check if ComfyUI is running."

            health_data = health_resp.json()
            if health_data.get("status") != "healthy":
                return "❌ ComfyUI service is unavailable. Please try again later."

            # Prepare generation request
            generation_data = {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "width": width,
                "height": height,
                "steps": steps,
                "cfg": cfg,
                "checkpoint": checkpoint,
                "wait_for_completion": True,
                "timeout_seconds": self.valves.timeout,
            }

            # Send generation request
            resp = requests.post(
                f"{self.valves.backend_url}/comfyui/generate",
                headers=self._backend_headers(),
                json=generation_data,
                timeout=self.valves.timeout + 5,
            )

            if resp.status_code != 200:
                try:
                    return f"❌ Image generation failed with HTTP {resp.status_code}."
                except Exception:
                    return f"❌ Image generation failed: HTTP {resp.status_code}"

            result = resp.json()

            if not result.get("success"):
                return "❌ Generation failed. Please try again later."

            # Extract generation info
            prompt_id = result.get("prompt_id")
            outputs = result.get("data", {}).get("outputs", {})
            parameters = result.get("data", {}).get("parameters", {})

            # Format success response
            output = []
            output.append("✅ **Image Generated Successfully!**")
            output.append(f"\n**Prompt:** {prompt}")
            if negative_prompt:
                output.append(f"**Negative Prompt:** {negative_prompt}")

            output.append(f"\n**Generation Parameters:**")
            output.append(f"- Model: {checkpoint}")
            output.append(f"- Dimensions: {width}×{height}")
            output.append(f"- Steps: {steps}")
            output.append(f"- CFG Scale: {cfg}")
            output.append(f"- Prompt ID: {prompt_id}")

            # If outputs contain images, try to get the first one
            if outputs:
                output.append(
                    f"\n**Generated Images:** {len(outputs)} image(s) created"
                )

                # Try to get the first image
                for node_id, node_output in outputs.items():
                    if isinstance(node_output, dict) and "images" in node_output:
                        images = node_output["images"]
                        if images and len(images) > 0:
                            first_image = images[0]
                            if isinstance(first_image, dict):
                                filename = first_image.get("filename")
                                if filename:
                                    output.append(
                                        f"\n**Image available:** {filename} (access via ComfyUI interface)"
                                    )
                                break
            else:
                output.append(
                    f"\n⚠️ Image generated but output data not available in response"
                )

            return "\n".join(output)

        except requests.exceptions.ConnectionError:
            return "❌ Cannot connect to backend service. Please check if the backend is running."
        except requests.exceptions.Timeout:
            return "❌ Image generation timed out. This can happen with complex prompts or high resolution images."
        except Exception:
            return "❌ Image generation failed unexpectedly. Please try again later."

    def get_available_models(self) -> str:
        """
        Get list of available ComfyUI models.

        :return: List of available models from the database
        """

        if not self.valves.enable_tool:
            return "❌ Image generation tool is currently disabled."

        try:
            resp = requests.get(
                f"{self.valves.backend_url}/comfyui/db/models?active_only=true",
                headers=self._backend_headers(),
                timeout=30,
            )

            if resp.status_code != 200:
                return f"❌ Failed to get models: HTTP {resp.status_code}"

            result = resp.json()

            if not result.get("success"):
                return "❌ Failed to retrieve models from database"

            models = result.get("models", [])

            if not models:
                return "⚠️ No models found in database"

            # Group models by type
            models_by_type = {}
            for model in models:
                model_type = model.get("type", "unknown")
                if model_type not in models_by_type:
                    models_by_type[model_type] = []
                models_by_type[model_type].append(model)

            output = ["📋 **Available ComfyUI Models:**\n"]

            for model_type, type_models in models_by_type.items():
                output.append(f"## {model_type.title()} Models")
                for model in type_models:
                    name = model.get("name", "Unknown")
                    filename = model.get("filename", "")
                    size_gb = model.get("file_size_gb")
                    essential = "⭐" if model.get("essential") else ""
                    description = model.get("description", "")

                    size_info = f" ({size_gb}GB)" if size_gb else ""
                    desc_info = f" - {description}" if description else ""

                    output.append(f"- **{name}** {essential}")
                    output.append(f"  - File: `{filename}`{size_info}")
                    if desc_info:
                        output.append(f"  - {description}")
                output.append("")

            output.append("⭐ = Essential/Recommended models")

            return "\n".join(output)

        except requests.exceptions.ConnectionError:
            return "❌ Cannot connect to backend service. Please check if the backend is running."
        except Exception:
            return "❌ Model retrieval failed. Please try again later."

    def check_comfyui_status(self) -> str:
        """
        Check ComfyUI service health and queue status.

        :return: Current status of ComfyUI service
        """

        if not self.valves.enable_tool:
            return "❌ Image generation tool is currently disabled."

        try:
            # Check health
            health_resp = requests.get(
                f"{self.valves.backend_url}/comfyui/health",
                headers=self._backend_headers(),
                timeout=10,
            )

            output = ["🔍 **ComfyUI Service Status**\n"]

            if health_resp.status_code != 200:
                output.append("❌ **Health Check:** Failed")
                output.append(f"- HTTP Status: {health_resp.status_code}")
                return "\n".join(output)

            health_data = health_resp.json()
            status = health_data.get("status", "unknown")

            if status == "healthy":
                output.append("✅ **Health Check:** Healthy")
            else:
                output.append(f"⚠️ **Health Check:** {status}")

            # Check queue status
            try:
                queue_resp = requests.get(
                    f"{self.valves.backend_url}/comfyui/queue",
                    headers=self._backend_headers(),
                    timeout=10,
                )

                if queue_resp.status_code == 200:
                    queue_data = queue_resp.json()
                    if queue_data.get("success"):
                        queue_info = queue_data.get("queue", {})
                        pending = len(queue_info.get("queue_pending", []))
                        running = len(queue_info.get("queue_running", []))

                        output.append(f"\n📊 **Queue Status:**")
                        output.append(f"- Pending jobs: {pending}")
                        output.append(f"- Running jobs: {running}")
                    else:
                        output.append(f"\n⚠️ **Queue Status:** Unable to retrieve")
                else:
                    output.append(
                        f"\n⚠️ **Queue Status:** HTTP {queue_resp.status_code}"
                    )

            except Exception:
                output.append("\n⚠️ **Queue Status:** Unavailable")

            output.append(f"\n🔧 **Configuration:**")
            output.append(f"- Timeout: {self.valves.timeout}s")
            output.append(
                f"- Default Size: {self.valves.default_width}×{self.valves.default_height}"
            )

            return "\n".join(output)

        except requests.exceptions.ConnectionError:
            return "❌ Cannot connect to backend service. Please check if the backend is running."
        except Exception:
            return "❌ ComfyUI status is unavailable. Please try again later."
