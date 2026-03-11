from __future__ import annotations

from whisper_ui.core.models import Job, JobStatus, Segment, TranscriptResult


def test_job_defaults():
    job = Job()
    assert job.status == JobStatus.PENDING
    assert job.progress == 0.0
    assert job.id
    assert job.created_at
    assert job.updated_at


def test_job_touch():
    job = Job()
    old_updated = job.updated_at
    job.touch()
    assert job.updated_at >= old_updated


def test_segment():
    seg = Segment(start=1.0, end=2.5, text="hello", speaker="SPEAKER_01")
    assert seg.start == 1.0
    assert seg.end == 2.5
    assert seg.text == "hello"
    assert seg.speaker == "SPEAKER_01"


def test_transcript_result():
    result = TranscriptResult(
        segments=[Segment(start=0, end=1, text="test")],
        language="en",
        duration=1.0,
    )
    assert len(result.segments) == 1
    assert result.language == "en"
    assert result.duration == 1.0


def test_transcript_result_to_dict():
    result = TranscriptResult(
        segments=[
            Segment(start=0.0, end=1.5, text="Hello", speaker="SPEAKER_00"),
            Segment(start=1.5, end=3.0, text="World"),
        ],
        language="en",
        duration=3.0,
    )
    d = result.to_dict()
    assert d["language"] == "en"
    assert d["duration"] == 3.0
    assert len(d["segments"]) == 2
    assert d["segments"][0] == {
        "start": 0.0,
        "end": 1.5,
        "text": "Hello",
        "speaker": "SPEAKER_00",
    }
    assert d["segments"][1]["speaker"] is None


def test_transcript_result_from_dict():
    data = {
        "language": "ja",
        "duration": 5.5,
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "Hello", "speaker": "SPEAKER_00"},
            {"start": 2.0, "end": 5.5, "text": "World", "speaker": None},
        ],
    }
    result = TranscriptResult.from_dict(data)
    assert result.language == "ja"
    assert result.duration == 5.5
    assert len(result.segments) == 2
    assert result.segments[0].text == "Hello"
    assert result.segments[0].speaker == "SPEAKER_00"
    assert result.segments[1].speaker is None


def test_transcript_result_roundtrip():
    original = TranscriptResult(
        segments=[Segment(start=1.0, end=2.0, text="test", speaker="S1")],
        language="en",
        duration=2.0,
    )
    restored = TranscriptResult.from_dict(original.to_dict())
    assert restored.language == original.language
    assert restored.duration == original.duration
    assert len(restored.segments) == len(original.segments)
    assert restored.segments[0].text == original.segments[0].text


def test_job_diarization_fields():
    job = Job(enable_diarization=False, convert_to_traditional=False)
    assert job.enable_diarization is False
    assert job.convert_to_traditional is False
    job2 = Job()
    assert job2.enable_diarization is True
    assert job2.convert_to_traditional is True


def test_job_status_values():
    assert JobStatus.PENDING == "pending"
    assert JobStatus.COMPLETED == "completed"
    assert JobStatus.FAILED == "failed"
