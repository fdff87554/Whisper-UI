"""Test-only convenience wrappers around FileStore / JobDatabase.

These used to live on the production classes (``FileStore.save_upload``,
``JobDatabase.list_jobs``) but no production path ever called them — the
upload route streams via ``prepare_upload_path`` and queries go through
``list_jobs_filtered``. They live here so the production API surface
reflects what production actually does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from whisper_ui.core.models import Job
    from whisper_ui.storage.database import JobDatabase
    from whisper_ui.storage.filestore import FileStore


def save_upload(filestore: FileStore, job_id: str, filename: str, data: bytes) -> Path:
    """Write upload bytes the way the upload route does, in one call."""
    dest = filestore.prepare_upload_path(job_id, filename)
    dest.write_bytes(data)
    return dest


def list_jobs(db: JobDatabase, *, limit: int = 50, offset: int = 0) -> list[Job]:
    """Unfiltered newest-first listing for test assertions."""
    return db.list_jobs_filtered(limit=limit, offset=offset)
