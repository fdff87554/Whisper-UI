from __future__ import annotations

import json
import shutil
from pathlib import Path

from whisper_ui.core.models import TranscriptResult


class FileStore:
    def __init__(self, upload_dir: Path, output_dir: Path) -> None:
        self._upload_dir = upload_dir
        self._output_dir = output_dir
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def save_upload(self, job_id: str, filename: str, data: bytes) -> Path:
        job_dir = self._upload_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        dest = job_dir / Path(filename).name
        dest.write_bytes(data)
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

    def delete_job_files(self, job_id: str) -> None:
        for base in (self._upload_dir, self._output_dir):
            job_dir = base / job_id
            if job_dir.exists():
                shutil.rmtree(job_dir)
