from __future__ import annotations

import streamlit as st

from whisper_ui.core.models import JobStatus, Segment, TranscriptResult
from whisper_ui.export.factory import available_formats, get_exporter
from whisper_ui.ui.labels import STATUS_LABELS, VIEWER_NO_SEGMENTS


def render_job_status_badge(status: JobStatus) -> None:
    colors = {
        JobStatus.PENDING: "gray",
        JobStatus.QUEUED: "blue",
        JobStatus.PROCESSING: "orange",
        JobStatus.COMPLETED: "green",
        JobStatus.FAILED: "red",
    }
    color = colors.get(status, "gray")
    label = STATUS_LABELS.get(status.value, status.value.upper())
    st.markdown(f":{color}[{label}]")


def render_progress(progress: float, message: str) -> None:
    st.progress(min(progress, 1.0), text=message)


def render_transcript(result: TranscriptResult) -> None:
    if not result.segments:
        st.info(VIEWER_NO_SEGMENTS)
        return

    for seg in result.segments:
        _render_segment(seg)


def _render_segment(seg: Segment) -> None:
    start_ts = _format_time(seg.start)
    end_ts = _format_time(seg.end)
    speaker_label = f"**[{seg.speaker}]** " if seg.speaker else ""
    st.markdown(f"`{start_ts} - {end_ts}` {speaker_label}{seg.text}")


def render_download_buttons(result: TranscriptResult, filename_base: str) -> None:
    cols = st.columns(len(available_formats()))
    for col, fmt in zip(cols, available_formats(), strict=True):
        exporter = get_exporter(fmt)
        data = exporter.export(result)
        col.download_button(
            label=f"{exporter.format_name}",
            data=data,
            file_name=f"{filename_base}{exporter.file_extension}",
            mime=exporter.mime_type,
        )


def _format_time(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"
