from __future__ import annotations

import logging
from typing import Any

from whisper_ui.core.models import Segment, TranscriptResult
from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)


class PostprocessStage:
    def __init__(self, convert_to_traditional: bool = False) -> None:
        self._convert_to_traditional = convert_to_traditional
        self._converter = None

    @property
    def name(self) -> str:
        return "postprocess"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        if on_progress:
            on_progress(0.0, "Post-processing results...")

        raw = context.get("final_result") or context.get("aligned_result") or context.get("transcription_result")
        if raw is None:
            context["transcript_result"] = TranscriptResult()
            if on_progress:
                on_progress(1.0, "No results to post-process.")
            return context

        segments = self._build_segments(raw)

        if self._convert_to_traditional:
            segments = self._convert_chinese(segments)

        result = TranscriptResult(
            segments=segments,
            language=context.get("language", "zh"),
            duration=context.get("duration", 0.0),
        )

        if on_progress:
            on_progress(1.0, "Post-processing complete.")

        context["transcript_result"] = result
        return context

    def cleanup(self) -> None:
        self._converter = None

    def _build_segments(self, raw: dict[str, Any]) -> list[Segment]:
        segments: list[Segment] = []
        for seg in raw.get("segments", []):
            segments.append(
                Segment(
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0),
                    text=seg.get("text", "").strip(),
                    speaker=seg.get("speaker"),
                )
            )
        return segments

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
