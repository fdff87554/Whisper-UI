from __future__ import annotations

import streamlit as st

from whisper_ui.ui.labels import PAGE_JOBS, PAGE_UPLOAD, PAGE_VIEWER


def main() -> None:
    st.set_page_config(
        page_title="Whisper UI",
        page_icon="",
        layout="wide",
    )

    upload_page = st.Page("pages/1_upload.py", title=PAGE_UPLOAD, default=True)
    jobs_page = st.Page("pages/2_jobs.py", title=PAGE_JOBS)
    viewer_page = st.Page("pages/3_viewer.py", title=PAGE_VIEWER)

    pg = st.navigation([upload_page, jobs_page, viewer_page])
    pg.run()


if __name__ == "__main__":
    main()
