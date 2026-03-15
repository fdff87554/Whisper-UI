from __future__ import annotations

import io
import zipfile
from pathlib import Path

from whisper_ui.core.models import Job, JobStatus
from whisper_ui.export.factory import get_exporter
from whisper_ui.storage.filestore import FileStore


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
    seen_names: dict[str, int] = {}
    count = 0

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for job in jobs:
            if job.status != JobStatus.COMPLETED:
                continue
            result = filestore.load_result(job.id)
            if result is None:
                continue
            base = Path(job.filename).stem
            if base in seen_names:
                seen_names[base] += 1
                base = f"{base} ({seen_names[base]})"
            else:
                seen_names[base] = 0
            zf.writestr(f"{base}{exporter.file_extension}", exporter.export(result))
            count += 1

    return buf.getvalue() if count > 0 else None
