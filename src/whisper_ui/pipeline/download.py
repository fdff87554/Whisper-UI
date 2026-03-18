from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from whisper_ui.core.constants import YT_DLP_SOCKET_TIMEOUT
from whisper_ui.core.exceptions import DownloadError
from whisper_ui.core.messages import DOWNLOAD_DONE, DOWNLOAD_EXTRACTING_INFO, DOWNLOAD_IN_PROGRESS
from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)


class DownloadStage:
    def __init__(self, *, max_duration: int = 14400) -> None:
        self._max_duration = max_duration

    @property
    def name(self) -> str:
        return "download"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        source_url = context.get("source_url")
        if not source_url:
            return context

        try:
            import yt_dlp
        except ImportError as err:
            raise DownloadError("yt-dlp is not installed.") from err

        download_dir = Path(context["download_dir"])
        download_dir.mkdir(parents=True, exist_ok=True)

        if on_progress:
            on_progress(0.0, DOWNLOAD_EXTRACTING_INFO)

        def progress_hook(d: dict[str, Any]) -> None:
            if not on_progress:
                return
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                if total > 0:
                    p = d["downloaded_bytes"] / total
                    on_progress(min(p, 0.99), DOWNLOAD_IN_PROGRESS)
            elif d["status"] == "finished":
                on_progress(1.0, DOWNLOAD_DONE)

        ydl_opts: dict[str, Any] = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": str(download_dir / "video.%(ext)s"),
            "merge_output_format": "mp4",
            "noplaylist": True,
            "socket_timeout": YT_DLP_SOCKET_TIMEOUT,
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(source_url, download=False)
                if info is None:
                    raise DownloadError("Failed to extract video information.")

                duration = info.get("duration") or 0
                if duration > self._max_duration:
                    hours = self._max_duration // 3600
                    raise DownloadError(f"Video duration ({duration}s) exceeds the maximum allowed ({hours}h).")

                info = ydl.extract_info(source_url, download=True)
                if info is None:
                    raise DownloadError("Download returned no information.")
        except DownloadError:
            raise
        except Exception as e:
            raise DownloadError(f"Failed to download audio: {e}") from e

        downloaded_files = list(download_dir.glob("video.*"))
        if not downloaded_files:
            raise DownloadError("Download completed but no video file was found.")

        context["input_path"] = str(downloaded_files[0])
        context["video_title"] = info.get("title", "")
        return context

    def cleanup(self) -> None:
        pass
