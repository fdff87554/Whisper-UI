from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from rq.timeouts import BaseTimeoutException

from whisper_ui.core.exceptions import PipelineError

if TYPE_CHECKING:
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

STAGE_WEIGHTS_WITH_DOWNLOAD: dict[str, tuple[float, float]] = {
    "download": (0.00, 0.15),
    "preprocess": (0.15, 0.20),
    "transcribe": (0.20, 0.60),
    "align": (0.60, 0.70),
    "diarize": (0.70, 0.90),
    "assign_speakers": (0.90, 0.95),
    "postprocess": (0.95, 1.00),
}

# Weight bands used when LLMCorrectionStage is appended. Kept separate from
# the default dicts so production jobs enqueued before an upgrade keep using
# the layout they started with — changing the existing dicts would cause
# their progress bars to jump when the worker is redeployed.
STAGE_WEIGHTS_WITH_LLM: dict[str, tuple[float, float]] = {
    "preprocess": (0.00, 0.05),
    "transcribe": (0.05, 0.50),
    "align": (0.50, 0.60),
    "diarize": (0.60, 0.85),
    "assign_speakers": (0.85, 0.90),
    "postprocess": (0.90, 0.92),
    "llm_correction": (0.92, 1.00),
}

STAGE_WEIGHTS_WITH_DOWNLOAD_AND_LLM: dict[str, tuple[float, float]] = {
    "download": (0.00, 0.12),
    "preprocess": (0.12, 0.17),
    "transcribe": (0.17, 0.55),
    "align": (0.55, 0.65),
    "diarize": (0.65, 0.85),
    "assign_speakers": (0.85, 0.90),
    "postprocess": (0.90, 0.92),
    "llm_correction": (0.92, 1.00),
}


class PipelineOrchestrator:
    def __init__(
        self,
        stages: list[PipelineStage],
        on_progress: ProgressCallback | None = None,
        stage_weights: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self._stages = stages
        self._on_progress = on_progress
        self._stage_weights = stage_weights or STAGE_WEIGHTS

    def run(self, context: dict[str, Any]) -> TranscriptResult:
        for stage in self._stages:
            stage_name = stage.name
            weight = self._stage_weights.get(stage_name, (0.0, 1.0))

            logger.info("Starting stage: %s", stage_name)

            def stage_progress(p: float, msg: str, _w: tuple[float, float] = weight) -> None:
                global_p = _w[0] + p * (_w[1] - _w[0])
                if self._on_progress:
                    self._on_progress(global_p, msg)

            try:
                context = stage.execute(context, on_progress=stage_progress)
            except BaseTimeoutException:
                # RQ's death penalty must propagate unchanged so the worker
                # task layer can classify it as a timeout rather than a
                # stage-specific failure.
                raise
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
