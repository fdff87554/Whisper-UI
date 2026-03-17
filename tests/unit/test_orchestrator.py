from __future__ import annotations

from typing import Any

import pytest

from whisper_ui.core.exceptions import PipelineError
from whisper_ui.core.models import Segment, TranscriptResult
from whisper_ui.pipeline.base import ProgressCallback
from whisper_ui.pipeline.orchestrator import PipelineOrchestrator


class FakeStage:
    def __init__(self, stage_name: str, result_key: str | None = None, result_value: Any = None):
        self._name = stage_name
        self._result_key = result_key
        self._result_value = result_value
        self.cleaned_up = False

    @property
    def name(self) -> str:
        return self._name

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        if on_progress:
            on_progress(1.0, f"{self._name} done")
        if self._result_key:
            context[self._result_key] = self._result_value
        return context

    def cleanup(self) -> None:
        self.cleaned_up = True


class FailingStage:
    @property
    def name(self) -> str:
        return "failing"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        raise ValueError("boom")

    def cleanup(self) -> None:
        pass


def test_orchestrator_runs_stages():
    result = TranscriptResult(segments=[Segment(start=0, end=1, text="hi")])
    stage = FakeStage("postprocess", "transcript_result", result)
    orchestrator = PipelineOrchestrator([stage])
    out = orchestrator.run({})
    assert out is result
    assert stage.cleaned_up


def test_orchestrator_progress():
    result = TranscriptResult(segments=[])
    stage = FakeStage("postprocess", "transcript_result", result)
    progress_log: list[tuple[float, str]] = []

    def on_progress(p: float, msg: str) -> None:
        progress_log.append((p, msg))

    orchestrator = PipelineOrchestrator([stage], on_progress=on_progress)
    orchestrator.run({})
    assert len(progress_log) > 0


def test_orchestrator_wraps_exception():
    orchestrator = PipelineOrchestrator([FailingStage()])
    with pytest.raises(PipelineError, match="boom"):
        orchestrator.run({})


def test_orchestrator_no_result():
    stage = FakeStage("postprocess")
    orchestrator = PipelineOrchestrator([stage])
    with pytest.raises(PipelineError, match="no transcript result"):
        orchestrator.run({})


def test_orchestrator_custom_stage_weights():
    result = TranscriptResult(segments=[])
    stage = FakeStage("postprocess", "transcript_result", result)
    custom_weights = {"postprocess": (0.50, 1.00)}
    progress_log: list[tuple[float, str]] = []

    def on_progress(p: float, msg: str) -> None:
        progress_log.append((p, msg))

    orchestrator = PipelineOrchestrator([stage], on_progress=on_progress, stage_weights=custom_weights)
    orchestrator.run({})
    assert len(progress_log) == 1
    # With custom weights, postprocess stage at p=1.0 should map to global 1.0
    assert progress_log[0][0] == pytest.approx(1.0)


def test_orchestrator_custom_weights_unknown_stage_uses_fallback():
    result = TranscriptResult(segments=[])
    stage = FakeStage("custom_stage", "transcript_result", result)
    custom_weights = {}  # No weight defined for custom_stage
    progress_log: list[tuple[float, str]] = []

    def on_progress(p: float, msg: str) -> None:
        progress_log.append((p, msg))

    orchestrator = PipelineOrchestrator([stage], on_progress=on_progress, stage_weights=custom_weights)
    orchestrator.run({})
    # Falls back to (0.0, 1.0) for unknown stages
    assert progress_log[0][0] == pytest.approx(1.0)
