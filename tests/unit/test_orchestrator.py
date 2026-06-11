from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.helpers.orchestrator import PipelineOrchestrator
from whisper_ui.core.exceptions import PipelineError

if TYPE_CHECKING:
    from whisper_ui.pipeline.base import ProgressCallback


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


def test_orchestrator_wraps_exception():
    orchestrator = PipelineOrchestrator([FailingStage()])
    with pytest.raises(PipelineError, match="boom"):
        orchestrator.run({})


def test_orchestrator_no_result():
    stage = FakeStage("postprocess")
    orchestrator = PipelineOrchestrator([stage])
    with pytest.raises(PipelineError, match="no transcript result"):
        orchestrator.run({})
