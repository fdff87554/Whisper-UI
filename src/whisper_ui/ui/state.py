from __future__ import annotations

import streamlit as st
from redis import Redis

from whisper_ui.core.config import Settings, get_settings
from whisper_ui.storage.database import JobDatabase
from whisper_ui.storage.filestore import FileStore


def get_config() -> Settings:
    if "settings" not in st.session_state:
        st.session_state.settings = get_settings()
    return st.session_state.settings


def get_db() -> JobDatabase:
    if "db" not in st.session_state:
        settings = get_config()
        st.session_state.db = JobDatabase(settings.database_path)
    return st.session_state.db


def get_filestore() -> FileStore:
    if "filestore" not in st.session_state:
        settings = get_config()
        st.session_state.filestore = FileStore(settings.upload_dir, settings.output_dir)
    return st.session_state.filestore


def get_redis() -> Redis:
    if "redis" not in st.session_state:
        settings = get_config()
        st.session_state.redis = Redis.from_url(settings.redis_url)
    return st.session_state.redis
