from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING, Any

from whisper_ui.core.constants import QUALITY_GATE_MIN_SEGMENTS, QUALITY_GATE_REPEAT_RATIO
from whisper_ui.core.languages import AUTO_LANGUAGE
from whisper_ui.core.messages import (
    POSTPROCESS_DONE,
    POSTPROCESS_EMPTY,
    POSTPROCESS_RUNNING,
    QUALITY_WARNING_REPETITIVE,
)
from whisper_ui.core.models import Segment, TranscriptResult

if TYPE_CHECKING:
    from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)


def _top_repeat_stats(segments: list[Segment]) -> tuple[int, float]:
    """Return (non-empty segment count, share of the most frequent text).

    Texts are normalized with strip+casefold so trivially-different repeats
    still count as one; empty texts are excluded entirely — silence yielding
    nothing is not a hallucination.
    """
    texts = [s.text.strip().casefold() for s in segments]
    texts = [t for t in texts if t]
    if not texts:
        return 0, 0.0
    top_count = Counter(texts).most_common(1)[0][1]
    return len(texts), top_count / len(texts)


def _resolve_language(context: dict[str, Any]) -> str:
    """Return the transcript's language, preferring what the model detected.

    Only the transcription result carries the detected code (whisperx's
    align/assign outputs drop the ``language`` key), so walk the result chain
    most-processed-first and fall back to the job's configured language. Two
    sentinels must never win over a real code: ``"unknown"`` (the whisper.cpp
    adapter's missing-language fallback, truthy but meaningless) and ``auto``
    (the configured value when detection was requested) — neither may reach
    the zh-only conversion gate or the persisted transcript as-is.
    """
    for key in ("final_result", "aligned_result", "transcription_result"):
        raw = context.get(key)
        if isinstance(raw, dict):
            detected = raw.get("language")
            if detected and detected != "unknown":
                return detected
    configured = context.get("language", "zh")
    return "unknown" if configured == AUTO_LANGUAGE else configured


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

        total, repeat_ratio = _top_repeat_stats(segments)
        if total >= QUALITY_GATE_MIN_SEGMENTS and repeat_ratio >= QUALITY_GATE_REPEAT_RATIO:
            percent = round(repeat_ratio * 100)
            context["quality_warning"] = QUALITY_WARNING_REPETITIVE.format(percent=percent, total=total)
            logger.warning(
                "quality gate tripped for job %s: %d%% of %d segments share one text",
                context.get("parent_job_id"),
                percent,
                total,
                extra={
                    "event": "quality_gate_tripped",
                    "job_id": context.get("parent_job_id"),
                    "repeat_ratio": round(repeat_ratio, 4),
                    "segments": total,
                },
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
