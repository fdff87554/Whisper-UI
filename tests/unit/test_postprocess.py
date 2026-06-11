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


def test_postprocess_name():
    assert PostprocessStage().name == "postprocess"
