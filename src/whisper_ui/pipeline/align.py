from __future__ import annotations

import gc
import logging
from typing import Any

from whisper_ui.core.exceptions import AlignmentError
from whisper_ui.core.messages import ALIGN_DONE, ALIGN_LOADING, ALIGN_RUNNING
from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)


class AlignStage:
    def __init__(self, device: str = "cuda") -> None:
        self._device = device
        self._model = None
        self._metadata = None

    @property
    def name(self) -> str:
        return "align"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        if on_progress:
            on_progress(0.0, ALIGN_LOADING)

        try:
            import whisperx

            transcription = context["transcription_result"]
            audio = context["whisperx_audio"]
            language = transcription.get("language", context.get("language", "zh"))

            self._model, self._metadata = whisperx.load_align_model(
                language_code=language,
                device=self._device,
            )

            if on_progress:
                on_progress(0.3, ALIGN_RUNNING)

            result = whisperx.align(
                transcription["segments"],
                self._model,
                self._metadata,
                audio,
                self._device,
                return_char_alignments=False,
            )

            if on_progress:
                on_progress(1.0, ALIGN_DONE)

            context["aligned_result"] = result
            return context

        except ImportError as err:
            raise AlignmentError("whisperx is not installed.") from err
        except Exception as e:
            raise AlignmentError(f"Alignment failed: {e}") from e

    def cleanup(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
        if self._metadata is not None:
            del self._metadata
            self._metadata = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
