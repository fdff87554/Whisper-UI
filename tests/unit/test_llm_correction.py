from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from whisper_ui.core.messages import (
    LLM_CORRECTION_DEGRADED,
    LLM_CORRECTION_SKIPPED,
)
from whisper_ui.core.models import Segment, TranscriptResult
from whisper_ui.pipeline.llm_correction import LLMCorrectionStage


@dataclass
class RecordedCall:
    model: str
    system: str
    user: str
    temperature: float
    keep_alive: str
    think: bool


@dataclass
class FakeOllamaClient:
    """Protocol-shaped fake that records calls and returns scripted responses."""

    responses: list[Any] = field(default_factory=list)
    calls: list[RecordedCall] = field(default_factory=list)
    closed: bool = False

    def chat_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float,
        keep_alive: str,
        think: bool,
    ) -> str:
        self.calls.append(
            RecordedCall(
                model=model, system=system, user=user, temperature=temperature, keep_alive=keep_alive, think=think
            )
        )
        if not self.responses:
            return '{"segments": []}'
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self) -> None:
        self.closed = True


def _make_transcript(texts: list[str]) -> TranscriptResult:
    return TranscriptResult(
        segments=[
            Segment(start=float(i), end=float(i + 1), text=text, speaker=f"SPK_{i % 2}") for i, text in enumerate(texts)
        ],
        language="zh",
        duration=float(len(texts)),
    )


def _make_stage(**overrides: Any) -> tuple[LLMCorrectionStage, FakeOllamaClient]:
    client = FakeOllamaClient()
    defaults: dict[str, Any] = {
        "base_url": "http://ollama:11434",
        "model": "gemma4:e2b",
        "keep_alive": "30m",
        "chunk_size": 5,
        "chunk_context": 2,
        "temperature": 0.1,
        "request_timeout": 60.0,
        "client": client,
    }
    defaults.update(overrides)
    return LLMCorrectionStage(**defaults), client


def _capture_progress() -> tuple[list[tuple[float, str]], Any]:
    calls: list[tuple[float, str]] = []

    def on_progress(progress: float, message: str) -> None:
        calls.append((progress, message))

    return calls, on_progress


def _valid_response_for(indices: list[int], corrected: list[str]) -> str:
    return json.dumps({"segments": [{"idx": idx, "text": text} for idx, text in zip(indices, corrected, strict=True)]})


def test_stage_skipped_when_base_url_empty():
    stage, client = _make_stage(base_url="")
    transcript = _make_transcript(["甲", "乙"])
    context = {"transcript_result": transcript, "language": "zh"}

    progress, on_progress = _capture_progress()
    result = stage.execute(context, on_progress)

    assert result is context
    assert [s.text for s in transcript.segments] == ["甲", "乙"]
    assert client.calls == []
    assert progress[-1] == (1.0, LLM_CORRECTION_SKIPPED)


def test_stage_skipped_when_language_not_zh():
    """The system prompt is crafted for Chinese; other languages must skip
    entirely to avoid feeding Chinese instructions to non-Chinese input.
    """
    stage, client = _make_stage()
    transcript = _make_transcript(["hello world", "good morning"])
    progress, on_progress = _capture_progress()

    stage.execute({"transcript_result": transcript, "language": "en"}, on_progress)

    assert client.calls == []
    assert [s.text for s in transcript.segments] == ["hello world", "good morning"]
    assert progress[-1] == (1.0, LLM_CORRECTION_SKIPPED)


def test_stage_skipped_when_language_missing():
    """Missing language key is treated the same as non-zh: skip the stage.
    This keeps the pipeline safe when older contexts don't supply the key.
    """
    stage, client = _make_stage()
    transcript = _make_transcript(["甲", "乙"])
    progress, on_progress = _capture_progress()

    stage.execute({"transcript_result": transcript}, on_progress)

    assert client.calls == []
    assert progress[-1] == (1.0, LLM_CORRECTION_SKIPPED)


def test_stage_skipped_when_transcript_missing():
    stage, client = _make_stage()
    progress, on_progress = _capture_progress()
    stage.execute({"language": "zh"}, on_progress)
    assert client.calls == []
    assert progress[-1] == (1.0, LLM_CORRECTION_SKIPPED)


def test_stage_skipped_when_segments_empty():
    stage, client = _make_stage()
    transcript = TranscriptResult(segments=[], language="zh")
    progress, on_progress = _capture_progress()
    stage.execute({"transcript_result": transcript, "language": "zh"}, on_progress)
    assert client.calls == []
    assert progress[-1] == (1.0, LLM_CORRECTION_SKIPPED)


def _zh_context(transcript: TranscriptResult, **extra: Any) -> dict[str, Any]:
    return {"transcript_result": transcript, "language": "zh", **extra}


def test_chunking_splits_with_context_window():
    stage, client = _make_stage(chunk_size=5, chunk_context=2)
    texts = [f"seg{i}" for i in range(12)]
    transcript = _make_transcript(texts)

    client.responses = [
        _valid_response_for([0, 1, 2, 3, 4], [f"x{i}" for i in range(5)]),
        _valid_response_for([5, 6, 7, 8, 9], [f"x{i}" for i in range(5, 10)]),
        _valid_response_for([10, 11], ["x10", "x11"]),
    ]

    stage.execute({"transcript_result": transcript, "language": "zh"})

    assert len(client.calls) == 3
    # First chunk: edit 0..4, ctx_before = [], ctx_after = [5, 6]
    first_user = client.calls[0].user
    assert 'EDIT: [{"idx": 0, "text": "seg0"}' in first_user
    assert "CONTEXT_BEFORE: []" in first_user
    assert '"idx": 5, "text": "seg5"' in first_user
    assert '"idx": 6, "text": "seg6"' in first_user
    # Middle chunk: edit 5..9, ctx_before = [3, 4], ctx_after = [10, 11]
    mid_user = client.calls[1].user
    assert '"idx": 3, "text": "seg3"' in mid_user
    assert '"idx": 4, "text": "seg4"' in mid_user
    assert '"idx": 10, "text": "seg10"' in mid_user
    # Last chunk: edit 10..11, ctx_before = [8, 9], ctx_after = []
    last_user = client.calls[2].user
    assert '"idx": 8, "text": "seg8"' in last_user
    assert '"idx": 9, "text": "seg9"' in last_user
    assert "CONTEXT_AFTER: []" in last_user


def test_applies_corrected_text_and_preserves_timings():
    stage, client = _make_stage(chunk_size=10, chunk_context=0)
    transcript = _make_transcript(["原文一", "原文二"])
    original_starts = [s.start for s in transcript.segments]
    original_ends = [s.end for s in transcript.segments]
    original_speakers = [s.speaker for s in transcript.segments]

    client.responses = [_valid_response_for([0, 1], ["校正一", "校正二"])]
    stage.execute({"transcript_result": transcript, "language": "zh"})

    assert [s.text for s in transcript.segments] == ["校正一", "校正二"]
    assert [s.start for s in transcript.segments] == original_starts
    assert [s.end for s in transcript.segments] == original_ends
    assert [s.speaker for s in transcript.segments] == original_speakers


def test_fallback_on_malformed_json():
    stage, client = _make_stage(chunk_size=10, chunk_context=0)
    transcript = _make_transcript(["甲", "乙"])
    client.responses = ["not json at all"]

    progress, on_progress = _capture_progress()
    stage.execute({"transcript_result": transcript, "language": "zh"}, on_progress)

    assert [s.text for s in transcript.segments] == ["甲", "乙"]
    assert progress[-1][0] == 1.0
    assert progress[-1][1] == LLM_CORRECTION_SKIPPED  # all chunks failed


def test_fallback_on_idx_mismatch_preserves_other_chunks():
    stage, client = _make_stage(chunk_size=2, chunk_context=0)
    transcript = _make_transcript(["甲", "乙", "丙", "丁"])
    # First chunk returns wrong idx -> reject; second chunk ok -> apply
    client.responses = [
        _valid_response_for([99, 100], ["X", "Y"]),
        _valid_response_for([2, 3], ["丙校", "丁校"]),
    ]

    progress, on_progress = _capture_progress()
    stage.execute({"transcript_result": transcript, "language": "zh"}, on_progress)

    assert [s.text for s in transcript.segments] == ["甲", "乙", "丙校", "丁校"]
    assert progress[-1][1].startswith(LLM_CORRECTION_DEGRADED)


def test_rq_timeout_propagates_from_llm_correction():
    """RQ death-penalty exceptions must NOT be swallowed by the per-chunk
    fallback. The broad ``except Exception`` catches everything up to but
    not including ``BaseTimeoutException``, which has to propagate so the
    worker task layer can classify the job as timed out. This guards the
    same class of bug that PR #34 Finding 1 had to fix for other stages.
    """
    from rq.timeouts import JobTimeoutException

    stage, client = _make_stage(chunk_size=10, chunk_context=0)
    transcript = _make_transcript(["甲", "乙"])
    client.responses = [JobTimeoutException("Task exceeded maximum timeout value (3600 seconds)")]

    with pytest.raises(JobTimeoutException):
        stage.execute({"transcript_result": transcript, "language": "zh"})


def test_fallback_on_http_error_never_raises():
    stage, client = _make_stage(chunk_size=10, chunk_context=0)
    transcript = _make_transcript(["甲", "乙"])
    client.responses = [httpx.ConnectError("boom")]

    progress, on_progress = _capture_progress()
    stage.execute({"transcript_result": transcript, "language": "zh"}, on_progress)

    assert [s.text for s in transcript.segments] == ["甲", "乙"]
    assert progress[-1] == (1.0, LLM_CORRECTION_SKIPPED)


def test_fallback_on_ollama_5xx_response():
    """A 500 from Ollama (raised by raise_for_status in the real client)
    must not poison the transcript — the chunk falls back to original text.
    """
    stage, client = _make_stage(chunk_size=10, chunk_context=0)
    transcript = _make_transcript(["甲", "乙"])
    fake_response = httpx.Response(500, request=httpx.Request("POST", "http://ollama/api/chat"))
    client.responses = [httpx.HTTPStatusError("server error", request=fake_response.request, response=fake_response)]

    progress, on_progress = _capture_progress()
    stage.execute({"transcript_result": transcript, "language": "zh"}, on_progress)

    assert [s.text for s in transcript.segments] == ["甲", "乙"]
    assert progress[-1] == (1.0, LLM_CORRECTION_SKIPPED)


def test_fallback_on_network_timeout():
    """A network-level read timeout (distinct from RQ's death penalty) must
    fall back per chunk rather than failing the whole job.
    """
    stage, client = _make_stage(chunk_size=10, chunk_context=0)
    transcript = _make_transcript(["甲", "乙"])
    client.responses = [httpx.ReadTimeout("read timeout")]

    progress, on_progress = _capture_progress()
    stage.execute({"transcript_result": transcript, "language": "zh"}, on_progress)

    assert [s.text for s in transcript.segments] == ["甲", "乙"]
    assert progress[-1] == (1.0, LLM_CORRECTION_SKIPPED)


def test_partial_5xx_keeps_succeeded_chunks_and_reports_degraded():
    """When some chunks 500 and others succeed, succeeded chunks stay
    corrected and the final progress message reflects degraded mode.
    """
    stage, client = _make_stage(chunk_size=2, chunk_context=0)
    transcript = _make_transcript(["甲", "乙", "丙", "丁"])
    fake_response = httpx.Response(500, request=httpx.Request("POST", "http://ollama/api/chat"))
    client.responses = [
        httpx.HTTPStatusError("upstream down", request=fake_response.request, response=fake_response),
        _valid_response_for([2, 3], ["丙校", "丁校"]),
    ]

    progress, on_progress = _capture_progress()
    stage.execute({"transcript_result": transcript, "language": "zh"}, on_progress)

    assert [s.text for s in transcript.segments] == ["甲", "乙", "丙校", "丁校"]
    assert progress[-1][1].startswith(LLM_CORRECTION_DEGRADED)


def test_progress_monotonic_and_ends_at_one():
    stage, client = _make_stage(chunk_size=2, chunk_context=0)
    transcript = _make_transcript(["a", "b", "c", "d", "e"])
    client.responses = [
        _valid_response_for([0, 1], ["A", "B"]),
        _valid_response_for([2, 3], ["C", "D"]),
        _valid_response_for([4], ["E"]),
    ]

    progress, on_progress = _capture_progress()
    stage.execute({"transcript_result": transcript, "language": "zh"}, on_progress)

    values = [p for p, _ in progress]
    assert values == sorted(values)
    assert values[-1] == 1.0


def test_request_parameters_passed_through():
    stage, client = _make_stage(chunk_size=10, chunk_context=0, temperature=0.1, keep_alive="1h")
    transcript = _make_transcript(["甲"])
    client.responses = [_valid_response_for([0], ["改"])]

    stage.execute({"transcript_result": transcript, "language": "zh"})

    call = client.calls[0]
    assert call.temperature == 0.1
    assert call.keep_alive == "1h"
    assert call.model == "gemma4:e2b"
    assert call.think is False  # default: thinking off for JSON correction
    assert "中文轉錄校對助理" in call.system


def test_think_flag_propagates_to_client():
    stage, client = _make_stage(chunk_size=10, chunk_context=0, think=True)
    transcript = _make_transcript(["甲"])
    client.responses = [_valid_response_for([0], ["改"])]

    stage.execute({"transcript_result": transcript, "language": "zh"})

    assert client.calls[0].think is True


def test_httpx_client_puts_think_at_payload_top_level():
    import json

    import httpx

    from whisper_ui.pipeline.llm_correction import HttpxOllamaClient

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": '{"segments": {}}'}})

    client = HttpxOllamaClient(base_url="http://ollama:11434", timeout=5.0)
    client._client = httpx.Client(base_url="http://ollama:11434", transport=httpx.MockTransport(handler))

    client.chat_json(model="m", system="s", user="u", temperature=0.1, keep_alive="30m", think=False)

    assert captured["body"]["think"] is False  # top-level
    assert "think" not in captured["body"]["options"]  # not nested under options


def test_aligned_result_not_touched():
    stage, client = _make_stage(chunk_size=10, chunk_context=0)
    transcript = _make_transcript(["甲"])
    aligned = {"segments": [{"start": 0.0, "end": 1.0, "text": "甲", "words": [{"word": "甲"}]}]}
    context = _zh_context(transcript, aligned_result=aligned)
    client.responses = [_valid_response_for([0], ["乙"])]

    stage.execute(context)

    assert context["aligned_result"] is aligned
    assert aligned["segments"][0]["text"] == "甲"
    assert aligned["segments"][0]["words"] == [{"word": "甲"}]


def test_cleanup_closes_owned_client_only():
    stage, client = _make_stage()
    stage.cleanup()
    assert client.closed is False  # injected client, not owned


def test_fallback_when_segments_key_missing():
    stage, client = _make_stage(chunk_size=10, chunk_context=0)
    transcript = _make_transcript(["甲", "乙"])
    client.responses = ['{"other": []}']

    stage.execute({"transcript_result": transcript, "language": "zh"})
    assert [s.text for s in transcript.segments] == ["甲", "乙"]


@pytest.mark.parametrize(
    "bad_payload",
    [
        '{"segments": [{"idx": "0", "text": "x"}]}',  # idx not int
        '{"segments": [{"idx": 0}]}',  # missing text
        '{"segments": [[0, "x"]]}',  # not dict entries
        '{"segments": "not-a-list"}',  # segments wrong outer type
    ],
)
def test_fallback_on_shape_errors(bad_payload: str):
    stage, client = _make_stage(chunk_size=10, chunk_context=0)
    transcript = _make_transcript(["甲"])
    client.responses = [bad_payload]

    stage.execute({"transcript_result": transcript, "language": "zh"})
    assert transcript.segments[0].text == "甲"
