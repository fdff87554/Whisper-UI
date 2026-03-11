from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SUPPORTED_DEVICES = {"cuda", "cpu"}
_CPU_INCOMPATIBLE_COMPUTE_TYPES = {"float16", "int8_float16"}


def detect_device(preferred: str = "auto") -> str:
    """Detect the best available compute device.

    Priority: user preference > CUDA > CPU.
    If *preferred* is not ``"auto"``, validate availability and fall back if needed.
    """
    if preferred == "cpu":
        return "cpu"

    cuda_available = _cuda_available()

    if preferred == "auto":
        device = "cuda" if cuda_available else "cpu"
        logger.info("Auto-detected device: %s", device)
        return device

    if preferred == "cuda":
        if cuda_available:
            return "cuda"
        logger.warning("CUDA requested but not available. Falling back to CPU.")
        return "cpu"

    # Unsupported device value
    logger.warning("Unsupported device '%s'. Falling back to CPU.", preferred)
    return "cpu"


def validate_compute_type(device: str, compute_type: str) -> str:
    """Validate compute_type compatibility with the device.

    CPU does not support float16 or int8_float16; downgrade to int8.
    """
    if device == "cpu" and compute_type in _CPU_INCOMPATIBLE_COMPUTE_TYPES:
        logger.warning(
            "compute_type '%s' is not supported on CPU. Falling back to int8.",
            compute_type,
        )
        return "int8"
    return compute_type


def release_gpu_memory() -> None:
    """Release GPU memory. Currently supports CUDA only."""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def _cuda_available() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except ImportError:
        return False
