from __future__ import annotations

import gc
import logging
from typing import Any

from whisper_ui.core.exceptions import TranscriptionError
from whisper_ui.core.messages import TRANSCRIBE_DONE, TRANSCRIBE_LOADING, TRANSCRIBE_RUNNING
from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)


class TranscribeStage:
    def __init__(self, model_name: str = "large-v3", compute_type: str = "int8_float16", device: str = "cuda") -> None:
        self._model_name = model_name
        self._compute_type = compute_type
        self._device = device
        self._model = None

    @property
    def name(self) -> str:
        return "transcribe"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        audio_path = context["audio_path"]
        language = context.get("language", "zh")
        batch_size = context.get("batch_size", 4)

        if on_progress:
            on_progress(0.0, TRANSCRIBE_LOADING)

        try:
            import whisperx

            self._model = whisperx.load_model(
                self._model_name,
                device=self._device,
                compute_type=self._compute_type,
                language=language,
            )

            if on_progress:
                on_progress(0.1, TRANSCRIBE_RUNNING)

            audio = whisperx.load_audio(audio_path)
            result = self._model.transcribe(audio, batch_size=batch_size, language=language)

            if on_progress:
                on_progress(1.0, TRANSCRIBE_DONE)

            context["transcription_result"] = result
            context["whisperx_audio"] = audio
            return context

        except ImportError as err:
            raise TranscriptionError("whisperx is not installed. Install it with: pip install whisperx") from err
        except Exception as e:
            raise TranscriptionError(f"Transcription failed: {e}") from e

    def cleanup(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
