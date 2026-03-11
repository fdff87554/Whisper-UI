from __future__ import annotations

import streamlit as st
from rq import Queue

from whisper_ui.core.constants import ERROR_MAX_LENGTH
from whisper_ui.core.models import LANGUAGE_LABELS, SUPPORTED_LANGUAGES, WHISPER_MODELS, Job, JobStatus
from whisper_ui.pipeline.preprocess import SUPPORTED_EXTENSIONS
from whisper_ui.ui.labels import (
    UPLOAD_CHOOSE_FILE,
    UPLOAD_CONVERT_TRADITIONAL,
    UPLOAD_CONVERT_TRADITIONAL_HELP,
    UPLOAD_DESCRIPTION,
    UPLOAD_DIARIZATION_HELP,
    UPLOAD_DIARIZATION_UNAVAILABLE,
    UPLOAD_ENABLE_DIARIZATION,
    UPLOAD_GO_TO_JOBS,
    UPLOAD_HEADER,
    UPLOAD_LANGUAGE,
    UPLOAD_MODEL,
    UPLOAD_NO_FILE,
    UPLOAD_NUM_SPEAKERS,
    UPLOAD_QUEUE_ERROR,
    UPLOAD_START,
    UPLOAD_SUBMITTED,
    UPLOAD_SUPPORTED_FORMATS,
)
from whisper_ui.ui.state import get_config, get_db, get_filestore, get_redis

st.header(UPLOAD_HEADER)

settings = get_config()
db = get_db()
filestore = get_filestore()

st.markdown(UPLOAD_DESCRIPTION)
st.caption(UPLOAD_SUPPORTED_FORMATS.format(formats=", ".join(sorted(SUPPORTED_EXTENSIONS))))

uploaded_file = st.file_uploader(
    UPLOAD_CHOOSE_FILE,
    type=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
)

default_model_index = WHISPER_MODELS.index(settings.whisper_model) if settings.whisper_model in WHISPER_MODELS else 0
default_lang_index = SUPPORTED_LANGUAGES.index(settings.language) if settings.language in SUPPORTED_LANGUAGES else 0
hf_token_available = bool(settings.hf_token)

with st.form("upload_form"):
    col1, col2, col3 = st.columns(3)
    with col1:
        language = st.selectbox(
            UPLOAD_LANGUAGE,
            SUPPORTED_LANGUAGES,
            index=default_lang_index,
            format_func=lambda code: LANGUAGE_LABELS.get(code, code),
        )
    with col2:
        model_name = st.selectbox(UPLOAD_MODEL, WHISPER_MODELS, index=default_model_index)
    with col3:
        num_speakers = st.number_input(
            UPLOAD_NUM_SPEAKERS,
            min_value=0,
            max_value=20,
            value=0,
        )

    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        enable_diarization = st.checkbox(
            UPLOAD_ENABLE_DIARIZATION,
            value=hf_token_available,
            disabled=not hf_token_available,
            help=UPLOAD_DIARIZATION_HELP,
        )
        if not hf_token_available:
            st.caption(UPLOAD_DIARIZATION_UNAVAILABLE)
    with col_opt2:
        convert_to_traditional = st.checkbox(
            UPLOAD_CONVERT_TRADITIONAL,
            value=(language == "zh"),
            help=UPLOAD_CONVERT_TRADITIONAL_HELP,
        )

    submitted = st.form_submit_button(UPLOAD_START)

if submitted and uploaded_file is not None:
    job = Job(
        filename=uploaded_file.name,
        language=language,
        model_name=model_name,
        num_speakers=num_speakers if num_speakers > 0 else None,
        enable_diarization=enable_diarization,
        convert_to_traditional=convert_to_traditional,
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
        st.success(UPLOAD_SUBMITTED.format(name=uploaded_file.name))
        st.info(UPLOAD_GO_TO_JOBS)
    except Exception as e:
        st.error(UPLOAD_QUEUE_ERROR.format(error=e))
        job.status = JobStatus.FAILED
        job.error = str(e)[:ERROR_MAX_LENGTH]
        db.update_job(job)

elif submitted and uploaded_file is None:
    st.warning(UPLOAD_NO_FILE)
