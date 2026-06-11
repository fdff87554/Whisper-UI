from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SUPPORTED_DEVICES = {"cuda", "rocm", "cpu"}
_CPU_INCOMPATIBLE_COMPUTE_TYPES = {"float16", "int8_float16"}


def detect_device(preferred: str = "auto") -> str:
    """Detect the best available compute device.

    Priority: user preference > CUDA > ROCm > CPU.
    ``"rocm"`` selects an AMD GPU through PyTorch's HIP build, which still
    exposes the ``torch.cuda.*`` API. Translate it to the string PyTorch and
    whisperx actually expect when placing tensors with :func:`torch_device_for`.
    If *preferred* is not ``"auto"``, validate availability and fall back if needed.
    """
    if preferred == "cpu":
        return "cpu"

    if preferred == "auto":
        if _cuda_available():
            device = "cuda"
        elif _rocm_available():
            device = "rocm"
        else:
            device = "cpu"
        logger.info("Auto-detected device: %s", device)
        return device

    if preferred == "cuda":
        if _cuda_available():
            return "cuda"
        if _rocm_available():
            # DEVICE=cuda on an AMD box is almost certainly a config slip;
            # falling back to CPU would silently waste the GPU.
            logger.warning("CUDA requested but this is a ROCm GPU. Using rocm; set DEVICE=rocm or auto to silence.")
            return "rocm"
        logger.warning("CUDA requested but not available. Falling back to CPU.")
        return "cpu"

    if preferred == "rocm":
        if _rocm_available():
            return "rocm"
        logger.warning("ROCm requested but not available. Falling back to CPU.")
        return "cpu"

    # Unsupported device value
    logger.warning("Unsupported device '%s'. Falling back to CPU.", preferred)
    return "cpu"


def validate_compute_type(device: str, compute_type: str) -> str:
    """Validate compute_type compatibility with the device.

    CPU does not support float16 or int8_float16; downgrade to int8. GPU
    devices (cuda / rocm) keep the requested type. Note the rocm worker
    transcribes via whisper.cpp, where ``compute_type`` is unused — it is kept
    here for API symmetry and the whisperx/CTranslate2 path on cuda.
    """
    if device == "cpu" and compute_type in _CPU_INCOMPATIBLE_COMPUTE_TYPES:
        logger.warning(
            "compute_type '%s' is not supported on CPU. Falling back to int8.",
            compute_type,
        )
        return "int8"
    return compute_type


def torch_device_for(device: str) -> str:
    """Map a logical device label to the string PyTorch / whisperx expect.

    PyTorch's ROCm build has no ``"rocm"`` device — AMD GPUs are addressed
    through the ``"cuda"`` namespace (HIP masquerades as CUDA) — so ``"rocm"``
    maps to ``"cuda"``. Anything other than a GPU label maps to ``"cpu"``.
    """
    if device in ("cuda", "rocm"):
        return "cuda"
    return "cpu"


def configure_torch_for_rocm() -> None:
    """Disable the MIOpen (cuDNN-equivalent) backend for ROCm workers.

    gfx1151 lacks working MIOpen kernels for some ops the pipeline relies on
    (e.g. pyannote SincNet's InstanceNorm raises ``miopenStatusUnknownError``).
    Turning the cuDNN backend off makes PyTorch fall back to native HIP
    kernels, which run correctly at a small performance cost. Idempotent and a
    no-op when torch is absent.
    """
    try:
        import torch

        torch.backends.cudnn.enabled = False
    except ImportError:
        pass


def release_gpu_memory() -> None:
    """Release cached GPU memory.

    Works for both CUDA and ROCm: PyTorch's HIP build exposes the same
    ``torch.cuda`` API, so ``torch.cuda.empty_cache()`` frees the AMD GPU too.
    """
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def _cuda_available() -> bool:
    """True only for a real NVIDIA CUDA build.

    A ROCm/HIP build also reports ``torch.cuda.is_available()`` True but sets
    ``torch.version.hip``; exclude it here so ``rocm`` is detected separately.
    """
    try:
        import torch

        return torch.cuda.is_available() and getattr(torch.version, "hip", None) is None
    except ImportError:
        return False


def _rocm_available() -> bool:
    """True when PyTorch is a ROCm/HIP build exposing a usable GPU."""
    try:
        import torch

        return torch.cuda.is_available() and getattr(torch.version, "hip", None) is not None
    except ImportError:
        return False
