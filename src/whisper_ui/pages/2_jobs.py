from __future__ import annotations

import streamlit as st

from whisper_ui.core.models import JobStatus
from whisper_ui.ui.components import render_job_status_badge, render_progress
from whisper_ui.ui.state import get_db, get_redis
from whisper_ui.worker.progress import RedisProgressReporter

st.header("Jobs")

db = get_db()
jobs = db.list_jobs(limit=50)

if not jobs:
    st.info("No jobs yet. Go to **Upload** to submit a file.")
    st.stop()


@st.fragment(run_every=2)
def job_list() -> None:
    redis = get_redis()
    for job in jobs:
        with st.container(border=True):
            col1, col2, col3 = st.columns([3, 1, 1])

            with col1:
                st.markdown(f"**{job.filename}**")
                st.caption(f"ID: {job.id[:8]}... | Language: {job.language} | Created: {job.created_at[:19]}")

            with col2:
                render_job_status_badge(job.status)

            with col3:
                if job.status == JobStatus.COMPLETED and st.button("View", key=f"view_{job.id}"):
                    st.session_state.view_job_id = job.id
                    st.switch_page("pages/3_viewer.py")

            if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
                progress_data = RedisProgressReporter.get_progress(redis, job.id)
                progress = float(progress_data.get("progress", "0"))
                message = progress_data.get("message", "Waiting...")
                render_progress(progress, message)

            if job.status == JobStatus.FAILED and job.error:
                st.error(f"Error: {job.error[:200]}")


job_list()
