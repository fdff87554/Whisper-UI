from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from rq.timeouts import BaseTimeoutException

from whisper_ui.core.exceptions import PipelineError
from whisper_ui.pipeline.progress_bands import DEFAULT_STAGE_WEIGHTS

if TYPE_CHECKING:
    from whisper_ui.core.models import TranscriptResult
    from whisper_ui.pipeline.base import PipelineStage, ProgressCallback

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    def __init__(
        self,
        stages: list[PipelineStage],
        on_progress: ProgressCallback | None = None,
        stage_weights: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self._stages = stages
        self._on_progress = on_progress
        self._stage_weights = stage_weights or DEFAULT_STAGE_WEIGHTS

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
                # RQ's death penalty must propagate unchanged so the
                # dispatcher can classify it as a timeout rather than a
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
