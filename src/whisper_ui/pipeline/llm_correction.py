"""Optional LLM-based post-processing stage for Whisper transcripts.

Runs after ``PostprocessStage`` and asks a small Ollama-hosted model to fix
obvious typos / homophones / punctuation errors in each segment's text,
without touching timing or speaker information.

Failure semantics:
- *Logic failures* (network errors, bad JSON, idx mismatch, etc.) fall back
  to the original text for that chunk. A successful transcription is never
  turned into a failed job by this stage.
- *RQ death-penalty timeouts* (``rq.timeouts.BaseTimeoutException``) must
  propagate unchanged — they indicate the whole job has exhausted its
  budget, so silently swallowing them would misreport a timed-out job as
  "completed with LLM correction skipped". This mirrors the convention
  already followed by ``AlignStage`` / ``AssignSpeakersStage`` /
  ``DownloadStage`` / ``DiarizeStage``.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from rq.timeouts import BaseTimeoutException

from whisper_ui.core.messages import (
    LLM_CORRECTION_DEGRADED,
    LLM_CORRECTION_DONE,
    LLM_CORRECTION_RUNNING,
    LLM_CORRECTION_SKIPPED,
)
from whisper_ui.core.models import Segment, TranscriptResult

if TYPE_CHECKING:
    from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "你是中文轉錄校對助理。只做以下事：修正明顯錯字、同音字與標點錯誤。\n"
    "禁止：改寫語氣/詞彙/語序、合併/拆分/刪除段落、改動專有名詞/數字/英文。\n"
    "若某段沒有明顯錯誤，原樣輸出。\n"
    '輸出必須為合法 JSON，格式 {"segments":[{"idx":<int>,"text":"<str>"}, ...]}，\n'
    "idx 必須與輸入 EDIT 區一一對應，數量必須相同，不得輸出任何多餘文字。"
)


class OllamaClient(Protocol):
    """Narrow interface used by ``LLMCorrectionStage`` — lets tests inject a fake."""

    def chat_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        temperature: float,
        keep_alive: str,
        think: bool,
    ) -> str: ...

    def close(self) -> None: ...


class HttpxOllamaClient:
    """Thin ``httpx`` wrapper around Ollama's ``/api/chat`` endpoint."""

    def __init__(self, base_url: str, timeout: float) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

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
        payload = {
            "model": model,
            "stream": False,
            "format": "json",
            "keep_alive": keep_alive,
            # Top-level (not an `options` key): Ollama's /api/chat reads `think`
            # to toggle a reasoning model's chain-of-thought. Off for correction.
            "think": think,
            "options": {
                "temperature": temperature,
                "top_p": 0.9,
                "num_predict": 2048,
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        response = self._client.post("/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()
        return data.get("message", {}).get("content", "")

    def close(self) -> None:
        self._client.close()


@dataclass
class _Chunk:
    edit_start: int
    edit_end: int
    context_before: list[tuple[int, str]]
    edit_items: list[tuple[int, str]]
    context_after: list[tuple[int, str]]


class LLMCorrectionStage:
    """Pipeline stage that rewrites ``transcript_result.segments[i].text`` in place.

    The stage is a no-op when ``base_url`` is empty, when no segments exist,
    or when every chunk fails — in all those cases it returns the context
    unchanged and reports progress 1.0. Segment timings and speaker labels
    are never touched.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        keep_alive: str,
        chunk_size: int,
        chunk_context: int,
        temperature: float,
        request_timeout: float,
        think: bool = False,
        client: OllamaClient | None = None,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._keep_alive = keep_alive
        self._chunk_size = chunk_size
        self._chunk_context = chunk_context
        self._temperature = temperature
        self._request_timeout = request_timeout
        self._think = think
        self._client = client
        self._owns_client = client is None

    @property
    def name(self) -> str:
        return "llm_correction"

    def execute(
        self,
        context: dict[str, Any],
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        transcript = context.get("transcript_result")
        # The system prompt is crafted for Traditional Chinese typo / homophone
        # correction. Running it on other languages would feed a Chinese
        # instruction to a model looking at English (or other) text, with
        # unpredictable results. Skip entirely for non-zh transcripts rather
        # than silently producing garbage. The gate reads the *detected*
        # language carried by the transcript (resolved by postprocess) rather
        # than the job's configured language, so ``language=auto`` jobs are
        # corrected when detection lands on zh and skipped otherwise.
        if not isinstance(transcript, TranscriptResult) or transcript.language != "zh":
            if on_progress:
                on_progress(1.0, LLM_CORRECTION_SKIPPED)
            return context
        if not self._base_url or not transcript.segments:
            if on_progress:
                on_progress(1.0, LLM_CORRECTION_SKIPPED)
            return context

        segments = transcript.segments
        chunks = self._build_chunks(segments)
        total = len(chunks)

        client = self._get_client()
        failed_chunks: list[int] = []
        for i, chunk in enumerate(chunks):
            try:
                corrections = self._correct_chunk(client, chunk)
                self._apply_corrections(segments, corrections)
            except BaseTimeoutException:
                # RQ's death penalty must propagate unchanged so the worker
                # task layer can classify the job as timed out instead of
                # masking it as a successful-with-llm-skipped run.
                raise
            except Exception as exc:
                failed_chunks.append(i)
                logger.warning(
                    "llm_correction chunk failed, preserving original text",
                    extra={
                        "chunk_index": i,
                        "chunks_total": total,
                        "error_class": type(exc).__name__,
                    },
                )
            if on_progress:
                done = i + 1
                on_progress(done / total, LLM_CORRECTION_RUNNING.format(done=done, total=total))

        if on_progress:
            if failed_chunks and len(failed_chunks) == total:
                on_progress(1.0, LLM_CORRECTION_SKIPPED)
            elif failed_chunks:
                on_progress(1.0, f"{LLM_CORRECTION_DEGRADED}{len(failed_chunks)}/{total}")
            else:
                on_progress(1.0, LLM_CORRECTION_DONE)
        return context

    def cleanup(self) -> None:
        if self._owns_client and self._client is not None:
            try:
                self._client.close()
            except Exception:
                logger.debug("llm_correction client close raised", exc_info=True)
            self._client = None

    def _get_client(self) -> OllamaClient:
        if self._client is None:
            self._client = HttpxOllamaClient(self._base_url, self._request_timeout)
        return self._client

    def _build_chunks(self, segments: list[Segment]) -> list[_Chunk]:
        chunks: list[_Chunk] = []
        n = len(segments)
        for start in range(0, n, self._chunk_size):
            end = min(start + self._chunk_size, n)
            ctx_before_start = max(0, start - self._chunk_context)
            ctx_after_end = min(n, end + self._chunk_context)
            chunks.append(
                _Chunk(
                    edit_start=start,
                    edit_end=end,
                    context_before=[(i, segments[i].text) for i in range(ctx_before_start, start)],
                    edit_items=[(i, segments[i].text) for i in range(start, end)],
                    context_after=[(i, segments[i].text) for i in range(end, ctx_after_end)],
                )
            )
        return chunks

    def _correct_chunk(self, client: OllamaClient, chunk: _Chunk) -> dict[int, str]:
        user_prompt = _build_user_prompt(chunk)
        raw_response = client.chat_json(
            model=self._model,
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            temperature=self._temperature,
            keep_alive=self._keep_alive,
            think=self._think,
        )
        return _parse_response(raw_response, {idx for idx, _ in chunk.edit_items})

    def _apply_corrections(self, segments: list[Segment], corrections: dict[int, str]) -> None:
        for idx, text in corrections.items():
            if 0 <= idx < len(segments):
                segments[idx].text = text


def _build_user_prompt(chunk: _Chunk) -> str:
    """Build the user-role message with CONTEXT_BEFORE / EDIT / CONTEXT_AFTER blocks."""
    return (
        "以下是轉錄結果。CONTEXT 區僅供參考，不要輸出；只對 EDIT 區的每一段進行校對。\n\n"
        f"CONTEXT_BEFORE: {json.dumps(_to_json_list(chunk.context_before), ensure_ascii=False)}\n"
        f"EDIT: {json.dumps(_to_json_list(chunk.edit_items), ensure_ascii=False)}\n"
        f"CONTEXT_AFTER: {json.dumps(_to_json_list(chunk.context_after), ensure_ascii=False)}"
    )


def _to_json_list(items: list[tuple[int, str]]) -> list[dict[str, Any]]:
    return [{"idx": idx, "text": text} for idx, text in items]


def _parse_response(raw: str, expected_idx: set[int]) -> dict[int, str]:
    """Parse and validate the LLM's JSON response.

    Returns a dict mapping ``idx`` to corrected text. Raises ``ValueError``
    on any malformed / mismatched response so the caller can fall back for
    the whole chunk.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or "segments" not in payload:
        raise ValueError("response missing 'segments' key")

    segments = payload["segments"]
    if not isinstance(segments, list):
        raise ValueError("'segments' is not a list")

    result: dict[int, str] = {}
    for item in segments:
        if not isinstance(item, dict):
            raise ValueError("segments entry is not an object")
        idx = item.get("idx")
        text = item.get("text")
        if not isinstance(idx, int) or not isinstance(text, str):
            raise ValueError("segments entry has invalid idx/text")
        result[idx] = text

    if set(result.keys()) != expected_idx:
        raise ValueError(f"returned idx set {sorted(result.keys())} does not match expected {sorted(expected_idx)}")
    return result
