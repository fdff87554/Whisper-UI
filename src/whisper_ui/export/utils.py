from __future__ import annotations

import re

# C0 control characters that XML 1.0 forbids; tab / newline / carriage
# return are XML-legal and excluded.
_XML_INCOMPATIBLE_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def strip_control_chars(text: str) -> str:
    """Drop control characters that python-docx (XML 1.0) rejects.

    python-docx raises ``ValueError("All strings must be XML compatible")``
    on these characters, turning one bad segment into a failed DOCX export.
    Like the newline case in :func:`collapse_newlines`, the realistic source
    is the optional LLM correction stage, not the ASR output itself.
    """
    return _XML_INCOMPATIBLE_CHARS.sub("", text)


def format_timestamp(seconds: float, ms_separator: str = ",") -> str:
    # Clamp negatives: floor-division/modulo on a negative float borrows and
    # produces an invalid timecode like "-1:59:59,500" that corrupts the cue
    # and any downstream subtitle parser. The normal pipeline emits only
    # non-negative offsets, so this just hardens against a degenerate segment,
    # matching the defensive posture of the sibling cue-text helpers.
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{ms_separator}{ms:03d}"


def escape_vtt_text(text: str) -> str:
    """Escape characters with structural meaning inside a WebVTT cue.

    ``&`` and ``<`` are reserved by the cue-text grammar, and a literal
    ``-->`` inside a cue line is parsed as a timing line. ``>`` is escaped
    as well so ``-->`` cannot survive in any form. Like the newline case in
    :func:`collapse_newlines`, the realistic source of these characters is
    the optional LLM correction stage, not the ASR output itself.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def neutralize_srt_arrows(text: str) -> str:
    """Replace a literal ``-->`` so it cannot mimic an SRT timing line.

    SRT has no escaping mechanism, so the arrow is substituted with the
    visually equivalent ``→``.
    """
    return text.replace("-->", "→")


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
