from __future__ import annotations

import logging
from typing import Any

from whisper_ui.core.exceptions import PipelineError
from whisper_ui.core.models import TranscriptResult
from whisper_ui.pipeline.base import PipelineStage, ProgressCallback

logger = logging.getLogger(__name__)

STAGE_WEIGHTS: dict[str, tuple[float, float]] = {
    "preprocess": (0.00, 0.05),
    "transcribe": (0.05, 0.55),
    "align": (0.55, 0.65),
    "diarize": (0.65, 0.90),
    "assign_speakers": (0.90, 0.95),
    "postprocess": (0.95, 1.00),
}


class PipelineOrchestrator:
    def __init__(self, stages: list[PipelineStage], on_progress: ProgressCallback | None = None) -> None:
        self._stages = stages
        self._on_progress = on_progress

    def run(self, context: dict[str, Any]) -> TranscriptResult:
        for stage in self._stages:
            stage_name = stage.name
            weight = STAGE_WEIGHTS.get(stage_name, (0.0, 1.0))

            logger.info("Starting stage: %s", stage_name)

            def stage_progress(p: float, msg: str, _w: tuple[float, float] = weight) -> None:
                global_p = _w[0] + p * (_w[1] - _w[0])
                if self._on_progress:
                    self._on_progress(global_p, msg)

            try:
                context = stage.execute(context, on_progress=stage_progress)
            except PipelineError:
                raise
            except Exception as e:
                raise PipelineError(f"Stage '{stage_name}' failed: {e}") from e
            finally:
                stage.cleanup()
                logger.info("Finished stage: %s", stage_name)

        result = context.get("transcript_result")
        if result is None:
            raise PipelineError("Pipeline completed but no transcript result was produced.")
        return result
