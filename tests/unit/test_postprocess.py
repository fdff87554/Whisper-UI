from __future__ import annotations

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


def test_postprocess_name():
    assert PostprocessStage().name == "postprocess"
