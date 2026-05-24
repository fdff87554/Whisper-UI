from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from whisper_ui.core.models import TranscriptResult

logger = logging.getLogger(__name__)


class FileStore:
    def __init__(self, upload_dir: Path, output_dir: Path) -> None:
        self._upload_dir = upload_dir
        self._output_dir = output_dir
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def prepare_upload_path(self, job_id: str, filename: str) -> Path:
        """Create the upload directory for *job_id* and return the destination path."""
        job_dir = self._upload_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir / Path(filename).name

    def save_upload(self, job_id: str, filename: str, data: bytes) -> Path:
        dest = self.prepare_upload_path(job_id, filename)
        dest.write_bytes(data)
        return dest

    def copy_source_for_new_job(self, src_job_id: str, src_filename: str, new_job_id: str) -> Path:
        """Copy ``src_job_id``'s uploaded audio into ``new_job_id``'s upload dir.

        Returns the destination path so the caller can set the new job's
        ``filepath`` without re-deriving it. Copies (rather than references)
        the file so each transcript version owns an independent upload dir;
        the strict all-or-nothing :meth:`delete_job_files` contract and the
        retention sweep then need no reference-counting.

        Raises ``FileNotFoundError`` when the source file is absent — e.g. the
        retention sweep already reclaimed it — so the caller can surface a
        clear "please re-upload" error instead of enqueuing a doomed job.
        """
        src_path = self.get_upload_path(src_job_id, src_filename)
        if not src_path.exists():
            raise FileNotFoundError(f"source audio for job {src_job_id} not found at {src_path}")
        dest = self.prepare_upload_path(new_job_id, src_path.name)
        shutil.copy2(src_path, dest)
        return dest

    def save_result(self, job_id: str, result: TranscriptResult) -> Path:
        job_dir = self._output_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        dest = job_dir / "result.json"
        dest.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return dest

    def load_result(self, job_id: str) -> TranscriptResult | None:
        path = self._output_dir / job_id / "result.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return TranscriptResult.from_dict(data)

    def get_upload_path(self, job_id: str, filename: str) -> Path:
        return self._upload_dir / job_id / Path(filename).name

    def get_output_dir(self, job_id: str) -> Path:
        return self._output_dir / job_id

    def get_source_media_path(self, job_id: str) -> Path | None:
        """Return the downloaded media file for a YouTube job, or None if not found.

        Searches for video.* first, then falls back to audio.* for backward
        compatibility with jobs downloaded before the video format change.
        """
        job_dir = self._upload_dir / job_id
        for pattern in ("video.*", "audio.*"):
            matches = list(job_dir.glob(pattern))
            if matches:
                return matches[0]
        return None

    def delete_job_files(self, job_id: str) -> None:
        """Remove both upload and output dirs for ``job_id``; raise on any failure.

        Manual delete routes (``DELETE /jobs/{id}``, ``DELETE /jobs/batch/{id}``)
        rely on this strict 'either both gone or both kept' contract: if a
        filesystem error leaves files behind, the route MUST NOT delete the
        DB row, otherwise the UI / audit log shows the job as deleted while
        the storage is still occupied — the regression PR #53 review F2
        flagged. Best-effort cleanup (which suits the retention sweep)
        belongs in :meth:`delete_upload_files`, not here.
        """
        removed: list[str] = []
        for base, label in ((self._upload_dir, "upload"), (self._output_dir, "output")):
            job_dir = base / job_id
            if not job_dir.exists():
                continue
            shutil.rmtree(job_dir)
            removed.append(label)
        if removed:
            logger.info(
                "filestore deleted job dirs for job_id=%s (%s)",
                job_id,
                "+".join(removed),
            )

    def delete_upload_files(self, job_id: str) -> bool:
        """Remove only the upload directory for ``job_id``; keep results.

        Returns True when the directory existed and was removed. Used by
        the optional retention task to reclaim disk on long-finished jobs
        while preserving the transcript and the DB row so the viewer
        keeps working.
        """
        job_dir = self._upload_dir / job_id
        if not job_dir.exists():
            return False
        try:
            shutil.rmtree(job_dir)
        except OSError as exc:
            logger.warning(
                "filestore upload-dir reclaim failed for job_id=%s: %s",
                job_id,
                exc.__class__.__name__,
            )
            return False
        logger.debug("filestore reclaimed upload dir for job_id=%s", job_id)
        return True
