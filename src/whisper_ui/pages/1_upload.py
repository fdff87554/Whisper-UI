from __future__ import annotations

import streamlit as st
from rq import Queue

from whisper_ui.core.models import Job, JobStatus
from whisper_ui.pipeline.preprocess import SUPPORTED_EXTENSIONS
from whisper_ui.ui.state import get_config, get_db, get_filestore, get_redis

st.header("Upload Audio")

settings = get_config()
db = get_db()
filestore = get_filestore()

st.markdown("Upload an audio or video file for transcription.")
st.caption(f"Supported formats: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

uploaded_file = st.file_uploader(
    "Choose a file",
    type=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
)

with st.form("upload_form"):
    col1, col2 = st.columns(2)
    with col1:
        language = st.selectbox("Language", ["zh", "en", "ja", "ko", "fr", "de", "es"], index=0)
    with col2:
        num_speakers = st.number_input(
            "Number of speakers (0 = auto)",
            min_value=0,
            max_value=20,
            value=0,
        )

    submitted = st.form_submit_button("Start Transcription")

if submitted and uploaded_file is not None:
    job = Job(
        filename=uploaded_file.name,
        language=language,
        num_speakers=num_speakers if num_speakers > 0 else None,
    )

    file_data = uploaded_file.read()
    dest = filestore.save_upload(job.id, uploaded_file.name, file_data)
    job.filepath = str(dest)
    job.status = JobStatus.QUEUED
    db.insert_job(job)

    try:
        redis = get_redis()
        q = Queue(connection=redis)
        q.enqueue(
            "whisper_ui.worker.tasks.process_transcription",
            job.id,
            job_timeout="1h",
        )
        st.success(f"Job submitted: **{uploaded_file.name}**")
        st.info("Go to the **Jobs** page to track progress.")
    except Exception as e:
        st.error(f"Failed to submit job to queue: {e}")
        job.status = JobStatus.FAILED
        job.error = str(e)[:1000]
        db.update_job(job)

elif submitted and uploaded_file is None:
    st.warning("Please upload a file first.")
