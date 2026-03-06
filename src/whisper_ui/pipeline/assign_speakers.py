from __future__ import annotations

import logging
from typing import Any

from whisper_ui.pipeline.base import ProgressCallback
from whisper_ui.ui.labels import ASSIGN_DONE, ASSIGN_FAILED, ASSIGN_RUNNING, ASSIGN_SKIPPED

logger = logging.getLogger(__name__)


class AssignSpeakersStage:
    @property
    def name(self) -> str:
        return "assign_speakers"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        if on_progress:
            on_progress(0.0, ASSIGN_RUNNING)

        diarize_result = context.get("diarize_result")
        aligned_result = context.get("aligned_result")

        if diarize_result is None or aligned_result is None:
            if on_progress:
                on_progress(1.0, ASSIGN_SKIPPED)
            return context

        try:
            import whisperx

            result = whisperx.assign_word_speakers(diarize_result, aligned_result)

            if on_progress:
                on_progress(1.0, ASSIGN_DONE)

            context["final_result"] = result
            return context

        except Exception as e:
            logger.warning("Speaker assignment failed: %s. Using aligned result without speakers.", e)
            context["final_result"] = aligned_result
            if on_progress:
                on_progress(1.0, ASSIGN_FAILED)
            return context

    def cleanup(self) -> None:
        pass
