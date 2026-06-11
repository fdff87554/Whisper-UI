from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from whisper_ui.core.messages import POSTPROCESS_DONE, POSTPROCESS_EMPTY, POSTPROCESS_RUNNING
from whisper_ui.core.models import Segment, TranscriptResult

if TYPE_CHECKING:
    from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)


def _resolve_language(context: dict[str, Any]) -> str:
    """Return the transcript's language, preferring what the model detected.

    Only the transcription result carries the detected code (whisperx's
    align/assign outputs drop the ``language`` key), so walk the result chain
    most-processed-first and fall back to the job's configured language. With
    ``language=auto`` the context value is the sentinel itself, which must
    never reach the s2t conversion gate or the persisted transcript.
    """
    for key in ("final_result", "aligned_result", "transcription_result"):
        raw = context.get(key)
        if isinstance(raw, dict) and raw.get("language"):
            return raw["language"]
    return context.get("language", "zh")


class PostprocessStage:
    """Convert WhisperX-style segments into a :class:`TranscriptResult`.

    Stage instances are constructed per-job by the orchestrator /
    dispatcher; ``_converter`` is therefore an instance attribute that
    is lazy-initialised on the first Chinese conversion and reused
    only within that single job. No two jobs share an OpenCC
    converter, so the stage does not need its own lock.
    """

    def __init__(self, convert_to_traditional: bool = False) -> None:
        self._convert_to_traditional = convert_to_traditional
        self._converter = None

    @property
    def name(self) -> str:
        return "postprocess"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        if on_progress:
            on_progress(0.0, POSTPROCESS_RUNNING)

        raw = context.get("final_result") or context.get("aligned_result") or context.get("transcription_result")
        if raw is None:
            context["transcript_result"] = TranscriptResult()
            if on_progress:
                on_progress(1.0, POSTPROCESS_EMPTY)
            return context

        segments = self._build_segments(raw)
        language = _resolve_language(context)

        if self._convert_to_traditional and language == "zh":
            segments = self._convert_chinese(segments)

        result = TranscriptResult(
            segments=segments,
            language=language,
            duration=context.get("duration", 0.0),
        )

        if on_progress:
            on_progress(1.0, POSTPROCESS_DONE)

        context["transcript_result"] = result
        return context

    def cleanup(self) -> None:
        self._converter = None

    def _build_segments(self, raw: dict[str, Any]) -> list[Segment]:
        return [
            Segment(
                start=seg.get("start", 0.0),
                end=seg.get("end", 0.0),
                text=seg.get("text", "").strip(),
                speaker=seg.get("speaker"),
            )
            for seg in raw.get("segments", [])
        ]

    def _convert_chinese(self, segments: list[Segment]) -> list[Segment]:
        try:
            from opencc import OpenCC

            if self._converter is None:
                self._converter = OpenCC("s2t")
            return [
                Segment(
                    start=s.start,
                    end=s.end,
                    text=self._converter.convert(s.text),
                    speaker=s.speaker,
                )
                for s in segments
            ]
        except ImportError:
            logger.warning("opencc-python-reimplemented not installed, skipping Chinese conversion.")
            return segments
