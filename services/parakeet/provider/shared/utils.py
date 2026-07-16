"""
Shared utilities for STT service
"""

import os
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

def get_device_info() -> Dict[str, Any]:
    """
    Get information about the current device (CPU/GPU/MPS)

    Returns:
        dict: Device information
    """
    import torch

    device_info = {
        "backend": os.getenv("PARAKEET_BACKEND", "cuda"),
        "device": os.getenv("PARAKEET_DEVICE", "cpu"),
        "cuda_available": torch.cuda.is_available(),
        "mps_available": torch.backends.mps.is_available() if hasattr(torch.backends, 'mps') else False,
    }

    if device_info["cuda_available"]:
        device_info["cuda_device_count"] = torch.cuda.device_count()
        device_info["cuda_device_name"] = torch.cuda.get_device_name(0)

    return device_info

def validate_audio_file(file_path: str) -> bool:
    """
    Validate that audio file exists and has supported format

    Args:
        file_path: Path to audio file

    Returns:
        bool: True if valid, False otherwise
    """
    if not os.path.exists(file_path):
        logger.error("Audio file not found")
        return False

    # Check file extension
    supported_formats = {'.wav', '.flac', '.mp3', '.m4a', '.ogg', '.opus'}
    _, ext = os.path.splitext(file_path)

    if ext.lower() not in supported_formats:
        logger.warning("Unsupported audio format")
        # Don't fail, let the transcription library handle it
        return True

    return True

def format_duration(seconds: float) -> str:
    """
    Format duration in seconds to human-readable string

    Args:
        seconds: Duration in seconds

    Returns:
        str: Formatted duration (e.g., "1:23.45")
    """
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}:{secs:05.2f}"
