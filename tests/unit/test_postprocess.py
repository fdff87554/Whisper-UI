from __future__ import annotations

from unittest.mock import MagicMock, patch

from whisper_ui.pipeline.postprocess import PostprocessStage


def test_postprocess_empty():
    stage = PostprocessStage()
    context = {"language": "zh", "duration": 0.0}
    result = stage.execute(context)
    transcript = result["transcript_result"]
    assert len(transcript.segments) == 0


def test_postprocess_builds_segments():
    stage = PostprocessStage()
    raw = {
        "segments": [
            {"start": 0.0, "end": 1.0, "text": " Hello ", "speaker": "SPEAKER_00"},
            {"start": 1.0, "end": 2.0, "text": "World"},
        ]
    }
    context = {"final_result": raw, "language": "en", "duration": 2.0}
    result = stage.execute(context)
    transcript = result["transcript_result"]
    assert len(transcript.segments) == 2
    assert transcript.segments[0].text == "Hello"
    assert transcript.segments[0].speaker == "SPEAKER_00"
    assert transcript.segments[1].speaker is None
    assert transcript.language == "en"
    assert transcript.duration == 2.0


def test_postprocess_skips_conversion_for_non_zh():
    """convert_to_traditional=True should have no effect when language is not zh."""
    stage = PostprocessStage(convert_to_traditional=True)
    raw = {"segments": [{"start": 0.0, "end": 1.0, "text": "Hello world"}]}
    context = {"final_result": raw, "language": "en", "duration": 1.0}
    result = stage.execute(context)
    assert result["transcript_result"].segments[0].text == "Hello world"


def test_postprocess_prefers_detected_language_over_context():
    """With language=auto the context holds the sentinel; the language the
    model actually detected (carried by the transcription result) must win.
    """
    stage = PostprocessStage()
    raw = {"language": "en", "segments": [{"start": 0.0, "end": 1.0, "text": "Hello"}]}
    context = {"transcription_result": raw, "language": "auto", "duration": 1.0}
    result = stage.execute(context)
    assert result["transcript_result"].language == "en"


def test_postprocess_converts_chinese_when_detected_language_is_zh():
    """The s2t gate must fire on the detected language, not the auto sentinel."""
    stage = PostprocessStage(convert_to_traditional=True)
    raw = {"language": "zh", "segments": [{"start": 0.0, "end": 1.0, "text": "简体中文"}]}
    context = {"transcription_result": raw, "language": "auto", "duration": 1.0}
    mock_opencc = MagicMock()
    mock_opencc.OpenCC.return_value.convert.side_effect = lambda text: f"[t]{text}"
    with patch.dict("sys.modules", {"opencc": mock_opencc}):
        result = stage.execute(context)
    assert result["transcript_result"].segments[0].text == "[t]简体中文"


def test_postprocess_falls_back_to_context_language_when_undetected():
    """whisperx's align/assign outputs drop the language key; a final_result
    without one must fall back to the job's configured language.
    """
    stage = PostprocessStage()
    raw = {"segments": [{"start": 0.0, "end": 1.0, "text": "你好"}]}
    context = {"final_result": raw, "language": "zh", "duration": 1.0}
    result = stage.execute(context)
    assert result["transcript_result"].language == "zh"


def test_postprocess_treats_unknown_sentinel_as_undetected():
    """The whisper.cpp adapter's truthy "unknown" must not beat the job's
    configured language — that would silently disable the zh-only s2t and
    LLM gates on an explicitly-zh job.
    """
    stage = PostprocessStage(convert_to_traditional=True)
    raw = {"language": "unknown", "segments": [{"start": 0.0, "end": 1.0, "text": "简体"}]}
    context = {"transcription_result": raw, "language": "zh", "duration": 1.0}
    mock_opencc = MagicMock()
    mock_opencc.OpenCC.return_value.convert.side_effect = lambda text: f"[t]{text}"
    with patch.dict("sys.modules", {"opencc": mock_opencc}):
        result = stage.execute(context)
    assert result["transcript_result"].language == "zh"
    assert result["transcript_result"].segments[0].text == "[t]简体"


def test_postprocess_auto_request_with_unknown_detection_persists_unknown():
    """When detection was requested but nothing was detected, the persisted
    language must be "unknown", never the "auto" sentinel itself.
    """
    stage = PostprocessStage()
    raw = {"language": "unknown", "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}]}
    context = {"transcription_result": raw, "language": "auto", "duration": 1.0}
    result = stage.execute(context)
    assert result["transcript_result"].language == "unknown"


def _raw_with_texts(texts: list[str]) -> dict:
    return {"segments": [{"start": float(i), "end": float(i + 1), "text": t} for i, t in enumerate(texts)]}


def test_quality_gate_flags_repetitive_transcript():
    stage = PostprocessStage()
    context = {
        "final_result": _raw_with_texts(["歡迎訂閱"] * 28 + ["真實內容", "另一句"]),
        "language": "zh",
        "duration": 30.0,
        "parent_job_id": "job-q",
    }
    result = stage.execute(context)
    warning = result["quality_warning"]
    assert "93%" in warning
    assert "30" in warning


def test_quality_gate_skips_normal_transcript():
    stage = PostprocessStage()
    texts = [f"第 {i} 句不同的內容" for i in range(30)]
    result = stage.execute({"final_result": _raw_with_texts(texts), "language": "zh", "duration": 30.0})
    assert "quality_warning" not in result


def test_quality_gate_skips_short_transcripts():
    """Below the minimum segment count even 100% repetition is not flagged:
    short clips legitimately produce few, similar segments."""
    stage = PostprocessStage()
    result = stage.execute({"final_result": _raw_with_texts(["同一句"] * 19), "language": "zh", "duration": 19.0})
    assert "quality_warning" not in result


def test_quality_gate_skips_ratio_below_threshold():
    texts = ["重複的句子"] * 49 + [f"獨特內容 {i}" for i in range(51)]
    stage = PostprocessStage()
    result = stage.execute({"final_result": _raw_with_texts(texts), "language": "zh", "duration": 100.0})
    assert "quality_warning" not in result


def test_quality_gate_ignores_empty_segment_texts():
    """Empty texts are not hallucinations; they must not count toward either
    side of the ratio. 25 empties + 19 identical stays under MIN_SEGMENTS."""
    stage = PostprocessStage()
    texts = ["   "] * 25 + ["同一句"] * 19
    result = stage.execute({"final_result": _raw_with_texts(texts), "language": "zh", "duration": 44.0})
    assert "quality_warning" not in result


def test_postprocess_name():
    assert PostprocessStage().name == "postprocess"
