from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from whisper_ui.core.models import Job, JobStatus
from whisper_ui.export.factory import get_exporter

if TYPE_CHECKING:
    from whisper_ui.storage.filestore import FileStore


def _zip_entry_base(job: Job) -> str:
    """Choose a ZIP entry base name that is safe across OSes.

    For uploaded media we keep the user's filename (already basename-
    sanitised at upload time) so the ZIP is self-describing. For URL
    jobs the canonical YouTube URL is stored as ``job.filename`` and
    would produce entries like ``watch?v=abc.srt`` — the ``?`` and
    ``=`` confuse Windows ZIP tools — so we fall back to the job id,
    which is the same identifier the viewer URL uses.
    """
    if job.source_url:
        return job.id
    return Path(job.filename).stem or job.id


def create_batch_zip(
    jobs: list[Job],
    filestore: FileStore,
    format_name: str,
) -> bytes | None:
    """Create an in-memory ZIP of exported results for completed jobs.

    Returns None if no results could be exported.
    """
    exporter = get_exporter(format_name)
    buf = io.BytesIO()
    used_filenames: set[str] = set()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for job in jobs:
            if job.status != JobStatus.COMPLETED:
                continue
            result = filestore.load_result(job.id)
            if result is None:
                continue
            base = _zip_entry_base(job)
            filename = f"{base}{exporter.file_extension}"
            counter = 1
            while filename in used_filenames:
                filename = f"{base} ({counter}){exporter.file_extension}"
                counter += 1
            used_filenames.add(filename)
            zf.writestr(filename, exporter.export(result))

    return buf.getvalue() if used_filenames else None
