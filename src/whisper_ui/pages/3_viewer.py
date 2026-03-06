from __future__ import annotations

from pathlib import Path

import streamlit as st

from whisper_ui.core.models import JobStatus
from whisper_ui.ui.components import render_download_buttons, render_transcript
from whisper_ui.ui.labels import (
    VIEWER_HEADER,
    VIEWER_METADATA,
    VIEWER_NO_COMPLETED,
    VIEWER_NOT_COMPLETED,
    VIEWER_NOT_FOUND,
    VIEWER_RESULT_NOT_FOUND,
    VIEWER_SELECT_JOB,
    VIEWER_TRANSCRIPT_TITLE,
)
from whisper_ui.ui.state import get_db, get_filestore

st.header(VIEWER_HEADER)

db = get_db()
filestore = get_filestore()

job_id = st.session_state.get("view_job_id")

if not job_id:
    jobs = db.list_jobs(limit=50)
    completed_jobs = [j for j in jobs if j.status == JobStatus.COMPLETED]
    if not completed_jobs:
        st.info(VIEWER_NO_COMPLETED)
        st.stop()

    selected = st.selectbox(
        VIEWER_SELECT_JOB,
        options=completed_jobs,
        format_func=lambda j: f"{j.filename} ({j.created_at[:19]})",
    )
    if selected:
        job_id = selected.id

if not job_id:
    st.stop()

job = db.get_job(job_id)
if job is None:
    st.error(VIEWER_NOT_FOUND)
    st.stop()

if job.status != JobStatus.COMPLETED:
    st.warning(VIEWER_NOT_COMPLETED.format(status=job.status.value))
    st.stop()

result = filestore.load_result(job_id)
if result is None:
    st.error(VIEWER_RESULT_NOT_FOUND)
    st.stop()

st.subheader(VIEWER_TRANSCRIPT_TITLE.format(name=job.filename))
if result.duration > 0:
    minutes = int(result.duration // 60)
    seconds = int(result.duration % 60)
    st.caption(
        VIEWER_METADATA.format(
            minutes=minutes,
            seconds=seconds,
            segments=len(result.segments),
            language=result.language,
        )
    )

filename_base = Path(job.filename).stem
render_download_buttons(result, filename_base)

st.divider()
render_transcript(result)
