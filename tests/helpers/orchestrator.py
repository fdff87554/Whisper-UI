"""Single-process pipeline orchestrator — test helper only.

This predates the Redis/RQ DAG dispatcher (worker/pipeline_dispatcher.py)
and is no longer used in production; it survives purely as a convenient way
for tests to run a list of stages end-to-end in one process. Keeping it
under tests/ keeps the shipped package free of an unused code path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from whisper_ui.core.exceptions import PipelineError
from whisper_ui.pipeline.progress_bands import build_stage_weights
from whisper_ui.worker.stage_tasks import _banded_progress, _execute_stage

if TYPE_CHECKING:
    from whisper_ui.core.models import TranscriptResult
    from whisper_ui.pipeline.base import PipelineStage, ProgressCallback

logger = logging.getLogger(__name__)


def _noop_progress(_p: float, _m: str) -> None:
    pass


class PipelineOrchestrator:
    def __init__(
        self,
        stages: list[PipelineStage],
        on_progress: ProgressCallback | None = None,
        stage_weights: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self._stages = stages
        self._on_progress = on_progress
        self._stage_weights = stage_weights or build_stage_weights(has_download=False, has_llm=False)

    def run(self, context: dict[str, Any]) -> TranscriptResult:
        # Reuse the production stage runner + band mapping so this helper cannot
        # drift from worker/stage_tasks on error classification, cleanup, or the
        # local->global progress formula (the two used to be copies).
        throttled = self._on_progress or _noop_progress
        for stage in self._stages:
            stage_name = stage.name
            weight = self._stage_weights.get(stage_name, (0.0, 1.0))
            logger.info("Starting stage: %s", stage_name)
            try:
                context = _execute_stage(
                    stage,
                    context,
                    _banded_progress(throttled, weight),
                    stage_name=stage_name,
                )
            finally:
                logger.info("Finished stage: %s", stage_name)

        result = context.get("transcript_result")
        if result is None:
            raise PipelineError("Pipeline completed but no transcript result was produced.")
        return result
