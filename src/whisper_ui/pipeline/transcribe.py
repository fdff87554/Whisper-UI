from __future__ import annotations

import gc
import inspect
import logging
from typing import TYPE_CHECKING, Any

from rq.timeouts import BaseTimeoutException

from whisper_ui.core.device import release_gpu_memory
from whisper_ui.core.exceptions import TranscriptionError
from whisper_ui.core.messages import TRANSCRIBE_DONE, TRANSCRIBE_LOADING, TRANSCRIBE_RUNNING

if TYPE_CHECKING:
    from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)

# Stage-internal progress band for whisperx's per-chunk callback. The 0.0-0.1
# slice covers model load; we leave 0.95-1.0 as headroom for the DONE message
# so the bar visibly settles after the last chunk lands.
_TRANSCRIBE_CALLBACK_START = 0.1
_TRANSCRIBE_CALLBACK_END = 0.95


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
                on_progress(_TRANSCRIBE_CALLBACK_START, TRANSCRIBE_RUNNING)

            audio = whisperx.load_audio(audio_path)

            transcribe_kwargs: dict[str, Any] = {"batch_size": batch_size, "language": language}
            if on_progress is not None and _supports_progress_callback(self._model.transcribe):
                transcribe_kwargs["progress_callback"] = _build_whisperx_progress_callback(on_progress)

            result = self._model.transcribe(audio, **transcribe_kwargs)

            if on_progress:
                on_progress(1.0, TRANSCRIBE_DONE)

            context["transcription_result"] = result
            context["whisperx_audio"] = audio
            return context

        except ImportError as err:
            raise TranscriptionError("whisperx is not installed. Install it with: pip install whisperx") from err
        except BaseTimeoutException:
            # Let RQ's death penalty propagate so the worker task classifies
            # it as a timeout instead of a stage-level transcription failure.
            raise
        except Exception as e:
            raise TranscriptionError(f"Transcription failed: {e}") from e

    def cleanup(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
            gc.collect()
            release_gpu_memory()


def _supports_progress_callback(transcribe_fn: Any) -> bool:
    """Detect whether the underlying whisperx transcribe accepts progress_callback.

    whisperx exposes the parameter from 3.4 onward, but the project pins it
    via ``pip install --no-deps`` in the worker image, so we cannot rely on a
    static version check. Probe the signature instead and fall back to the
    coarse 3-point progress on older builds.
    """
    try:
        return "progress_callback" in inspect.signature(transcribe_fn).parameters
    except (TypeError, ValueError):
        return False


def _build_whisperx_progress_callback(on_progress: ProgressCallback):
    """Adapt whisperx's 0-100 percent callback to our 0.0-1.0 stage protocol."""

    def _forward(percent: float) -> None:
        clamped = max(0.0, min(100.0, float(percent))) / 100.0
        stage_progress = _TRANSCRIBE_CALLBACK_START + clamped * (_TRANSCRIBE_CALLBACK_END - _TRANSCRIBE_CALLBACK_START)
        on_progress(stage_progress, TRANSCRIBE_RUNNING)

    return _forward
