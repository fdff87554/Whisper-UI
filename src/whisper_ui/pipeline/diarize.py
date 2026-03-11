from __future__ import annotations

import gc
import logging
from typing import Any

from whisper_ui.core.exceptions import DiarizationError
from whisper_ui.core.messages import (
    DIARIZE_DONE,
    DIARIZE_LOADING,
    DIARIZE_RUNNING,
    DIARIZE_SKIPPED,
)
from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)


class DiarizeStage:
    def __init__(self, hf_token: str = "", device: str = "cuda") -> None:
        self._hf_token = hf_token
        self._device = device
        self._pipeline = None

    @property
    def name(self) -> str:
        return "diarize"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        if not self._hf_token:
            logger.warning("No HF_TOKEN provided, skipping diarization.")
            context["diarize_result"] = None
            if on_progress:
                on_progress(1.0, DIARIZE_SKIPPED)
            return context

        if on_progress:
            on_progress(0.0, DIARIZE_LOADING)

        try:
            from whisperx.diarize import DiarizationPipeline

            self._pipeline = DiarizationPipeline(
                token=self._hf_token,
                device=self._device,
            )

            if on_progress:
                on_progress(0.2, DIARIZE_RUNNING)

            audio_path = context["audio_path"]
            num_speakers = context.get("num_speakers")

            kwargs: dict[str, Any] = {"audio": audio_path}
            if num_speakers is not None:
                kwargs["num_speakers"] = num_speakers

            diarize_segments = self._pipeline(**kwargs)

            if on_progress:
                on_progress(1.0, DIARIZE_DONE)

            context["diarize_result"] = diarize_segments
            return context

        except ImportError as err:
            raise DiarizationError("whisperx is not installed.") from err
        except Exception as e:
            raise DiarizationError(f"Diarization failed: {e}") from e

    def cleanup(self) -> None:
        if self._pipeline is not None:
            del self._pipeline
            self._pipeline = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
