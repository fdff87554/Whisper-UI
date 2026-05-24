from __future__ import annotations


def format_timestamp(seconds: float, ms_separator: str = ",") -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{ms_separator}{ms:03d}"


def collapse_newlines(text: str) -> str:
    """Collapse any embedded line breaks into single spaces.

    SRT and VTT are newline-delimited cue formats: a blank line terminates a
    cue, so a newline inside ``segment.text`` (which the optional LLM
    correction stage can emit) would split or truncate the cue. Collapsing to
    spaces keeps each cue on a single line and the output well-formed.

    Only line boundaries are touched; ordinary spacing inside the text is
    preserved.
    """
    return " ".join(text.splitlines())
