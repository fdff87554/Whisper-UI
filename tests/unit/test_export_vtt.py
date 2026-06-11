from __future__ import annotations

from whisper_ui.core.models import Segment, TranscriptResult
from whisper_ui.export.vtt import VttExporter


def test_vtt_export():
    result = TranscriptResult(
        segments=[
            Segment(start=0.0, end=1.5, text="Hello", speaker="SPEAKER_00"),
            Segment(start=2.0, end=3.0, text="World"),
        ],
    )
    exporter = VttExporter()
    data = exporter.export(result).decode("utf-8")
    assert data.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.500" in data
    assert "<v SPEAKER_00>Hello" in data
    assert "World" in data


def test_vtt_format_metadata():
    exporter = VttExporter()
    assert exporter.format_name == "VTT"
    assert exporter.file_extension == ".vtt"
    assert exporter.mime_type == "text/vtt"


def test_vtt_collapses_newlines_in_text_to_keep_cue_single_line():
    # A newline inside the text would otherwise terminate the cue early at the
    # blank-line delimiter; collapse it so the cue stays on one line.
    result = TranscriptResult(
        segments=[Segment(start=0.0, end=1.5, text="line one\nline two", speaker="SPEAKER_00")],
    )
    lines = VttExporter().export(result).decode("utf-8").split("\n")

    assert lines == ["WEBVTT", "", "00:00:00.000 --> 00:00:01.500", "<v SPEAKER_00>line one line two", ""]


def test_vtt_escapes_structural_characters_in_cue_text():
    # "&" and "<" are reserved in VTT cue text and a literal "-->" would be
    # parsed as a timing line; all must come back escaped while the voice tag
    # itself stays real markup.
    result = TranscriptResult(
        segments=[Segment(start=0.0, end=1.0, text="a < b & c --> d", speaker="SPEAKER_00")],
    )
    data = VttExporter().export(result).decode("utf-8")

    cue_line = data.split("\n")[3]
    assert cue_line == "<v SPEAKER_00>a &lt; b &amp; c --&gt; d"
