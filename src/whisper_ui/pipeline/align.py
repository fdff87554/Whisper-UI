from __future__ import annotations

import gc
import logging
from typing import TYPE_CHECKING, Any

from rq.timeouts import BaseTimeoutException

from whisper_ui.core.device import release_gpu_memory
from whisper_ui.core.exceptions import AlignmentError
from whisper_ui.core.messages import ALIGN_DONE, ALIGN_LOADING, ALIGN_RUNNING, ALIGN_SKIPPED

if TYPE_CHECKING:
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

        language = context.get("language", "unknown")
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
        except BaseTimeoutException:
            # RQ's death penalty must never be swallowed by the "degrade to
            # unaligned" fallback — otherwise the job would silently keep
            # running past its deadline. Propagate unchanged.
            raise
        except Exception as e:
            logger.warning(
                "Alignment failed for language '%s', continuing without alignment: %s",
                language,
                e,
                exc_info=True,
            )
            if on_progress:
                on_progress(1.0, ALIGN_SKIPPED)
            return context

    def cleanup(self) -> None:
        had_resources = self._model is not None or self._metadata is not None
        if self._model is not None:
            del self._model
            self._model = None
        if self._metadata is not None:
            del self._metadata
            self._metadata = None
        if had_resources:
            gc.collect()
            release_gpu_memory()
