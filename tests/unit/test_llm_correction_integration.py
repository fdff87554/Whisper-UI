"""Integration test: orchestrator runs postprocess + LLM correction end-to-end.

Uses a minimal fake stage chain and injects a fake Ollama client into
``LLMCorrectionStage`` to verify the new weight bands and the final
mutated ``TranscriptResult``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tests.helpers.orchestrator import PipelineOrchestrator
from whisper_ui.core.models import Segment, TranscriptResult
from whisper_ui.pipeline.llm_correction import LLMCorrectionStage
from whisper_ui.pipeline.progress_bands import build_stage_weights

if TYPE_CHECKING:
    from whisper_ui.pipeline.base import ProgressCallback


class _SeedPostprocessStage:
    """Stands in for the real postprocess stage — seeds transcript_result."""

    def __init__(self, transcript: TranscriptResult) -> None:
        self._transcript = transcript

    @property
    def name(self) -> str:
        return "postprocess"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        if on_progress:
            on_progress(1.0, "postprocess done")
        context["transcript_result"] = self._transcript
        return context

    def cleanup(self) -> None:
        pass


class _FakeOllamaClient:
    """Returns pre-scripted JSON for each incoming chat_json call."""

    def __init__(self, corrections: dict[int, str]) -> None:
        self._corrections = corrections
        self.closed = False

    def chat_json(self, *, model: str, system: str, user: str, temperature: float, keep_alive: str) -> str:
        # Pull the EDIT block's indices back out so we only "correct" the
        # ones the stage asked about; we cheat by returning everything our
        # fixture knows, filtered to what was requested.
        requested = []
        for line in user.splitlines():
            if line.startswith("EDIT: "):
                payload = json.loads(line[len("EDIT: ") :])
                requested = [item["idx"] for item in payload]
                break
        return json.dumps({"segments": [{"idx": idx, "text": self._corrections[idx]} for idx in requested]})

    def close(self) -> None:
        self.closed = True


def test_orchestrator_runs_postprocess_then_llm_correction():
    original = TranscriptResult(
        segments=[
            Segment(start=0.0, end=1.0, text="原始一", speaker="SPK_0"),
            Segment(start=1.0, end=2.0, text="原始二", speaker="SPK_1"),
            Segment(start=2.0, end=3.0, text="原始三", speaker="SPK_0"),
        ],
        language="zh",
        duration=3.0,
    )

    fake_client = _FakeOllamaClient({0: "校正一", 1: "校正二", 2: "校正三"})
    llm_stage = LLMCorrectionStage(
        base_url="http://fake-ollama:11434",
        model="gemma4:e2b",
        keep_alive="30m",
        chunk_size=2,
        chunk_context=1,
        temperature=0.1,
        request_timeout=30.0,
        client=fake_client,
    )

    progress_log: list[tuple[float, str]] = []

    def on_progress(progress: float, message: str) -> None:
        progress_log.append((progress, message))

    weights = build_stage_weights(has_download=False, has_llm=True)
    orchestrator = PipelineOrchestrator(
        [_SeedPostprocessStage(original), llm_stage],
        on_progress=on_progress,
        stage_weights=weights,
    )

    result = orchestrator.run({"language": "zh"})

    # Corrected text applied in-place; timings and speakers preserved.
    assert [s.text for s in result.segments] == ["校正一", "校正二", "校正三"]
    assert [s.start for s in result.segments] == [0.0, 1.0, 2.0]
    assert [s.speaker for s in result.segments] == ["SPK_0", "SPK_1", "SPK_0"]

    # Overall progress monotonic, ends at 1.0, final stage falls into the llm band.
    values = [p for p, _ in progress_log]
    assert values == sorted(values)
    assert values[-1] == 1.0
    llm_band = weights["llm_correction"]
    # At least one progress point must lie within the llm_correction band.
    assert any(llm_band[0] <= p <= llm_band[1] for p, _ in progress_log)
