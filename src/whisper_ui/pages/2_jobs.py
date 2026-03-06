from __future__ import annotations

import streamlit as st
from rq import Queue

from whisper_ui.core.models import JobStatus
from whisper_ui.ui.components import render_job_status_badge, render_progress
from whisper_ui.ui.labels import (
    JOBS_EMPTY,
    JOBS_ERROR,
    JOBS_HEADER,
    JOBS_RETRY,
    JOBS_RETRY_CONFIRM,
    JOBS_RETRY_CONFIRM_BUTTON,
    JOBS_RETRY_ERROR,
    JOBS_RETRY_SUBMITTED,
    JOBS_VIEW,
    JOBS_WAITING,
)
from whisper_ui.ui.state import get_db, get_redis
from whisper_ui.worker.progress import RedisProgressReporter

st.header(JOBS_HEADER)


@st.fragment(run_every=2)
def job_list() -> None:
    db = get_db()
    jobs = db.list_jobs(limit=50)
    redis = get_redis()

    if not jobs:
        st.info(JOBS_EMPTY)
        return

    for job in jobs:
        with st.container(border=True):
            col1, col2, col3 = st.columns([3, 1, 1])

            with col1:
                st.markdown(f"**{job.filename}**")
                st.caption(f"ID: {job.id[:8]}... | {job.language} | {job.model_name} | {job.created_at[:19]}")

            with col2:
                render_job_status_badge(job.status)

            with col3:
                if job.status == JobStatus.COMPLETED and st.button(JOBS_VIEW, key=f"view_{job.id}"):
                    st.session_state.view_job_id = job.id
                    st.switch_page("pages/3_viewer.py")

                if job.status == JobStatus.FAILED:
                    with st.popover(JOBS_RETRY, key=f"retry_pop_{job.id}"):
                        st.markdown(JOBS_RETRY_CONFIRM)
                        if st.button(
                            JOBS_RETRY_CONFIRM_BUTTON,
                            key=f"retry_confirm_{job.id}",
                            type="primary",
                        ):
                            try:
                                job.status = JobStatus.QUEUED
                                job.error = None
                                job.progress = 0.0
                                job.progress_message = ""
                                job.result_path = None
                                job.duration = None
                                db.update_job(job)
                                redis.delete(f"job:{job.id}")
                                q = Queue(connection=redis)
                                q.enqueue(
                                    "whisper_ui.worker.tasks.process_transcription",
                                    job.id,
                                    job_timeout="1h",
                                )
                                st.toast(JOBS_RETRY_SUBMITTED.format(name=job.filename))
                                st.rerun()
                            except Exception as e:
                                job.status = JobStatus.FAILED
                                job.error = str(e)[:1000]
                                db.update_job(job)
                                st.error(JOBS_RETRY_ERROR.format(error=e))

            if job.status in (JobStatus.QUEUED, JobStatus.PROCESSING):
                progress_data = RedisProgressReporter.get_progress(redis, job.id)
                progress = float(progress_data.get("progress", "0"))
                message = progress_data.get("message", JOBS_WAITING)
                render_progress(progress, message)

            if job.status == JobStatus.FAILED and job.error:
                st.error(JOBS_ERROR.format(error=job.error[:200]))


job_list()
