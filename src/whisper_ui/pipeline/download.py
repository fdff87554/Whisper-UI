from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from rq.timeouts import BaseTimeoutException

from whisper_ui.core.constants import YT_DLP_SOCKET_TIMEOUT
from whisper_ui.core.exceptions import DownloadError
from whisper_ui.core.messages import (
    DOWNLOAD_DONE,
    DOWNLOAD_EXTRACTING_INFO,
    DOWNLOAD_GDRIVE_IN_PROGRESS,
    DOWNLOAD_IN_PROGRESS,
)

if TYPE_CHECKING:
    from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)

_GDRIVE_HOSTS = frozenset({"drive.google.com", "docs.google.com"})


def _is_google_drive_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        return host in _GDRIVE_HOSTS
    except Exception:
        return False


def _extract_gdrive_file_id(url: str) -> str | None:
    """Extract the Google Drive file ID from a canonical or sharing URL."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    id_list = qs.get("id")
    if id_list:
        return id_list[0]
    match = re.search(r"/d/([a-zA-Z0-9_-]{10,})", parsed.path)
    return match.group(1) if match else None


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

        if _is_google_drive_url(source_url):
            return self._download_google_drive(source_url, context, on_progress)
        return self._download_youtube(source_url, context, on_progress)

    def _download_google_drive(
        self,
        source_url: str,
        context: dict[str, Any],
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        try:
            import gdown
        except ImportError as err:
            raise DownloadError("gdown is not installed.") from err

        download_dir = Path(context["download_dir"])
        download_dir.mkdir(parents=True, exist_ok=True)

        if on_progress:
            on_progress(0.0, DOWNLOAD_EXTRACTING_INFO)

        file_id = _extract_gdrive_file_id(source_url)
        if not file_id:
            raise DownloadError("Could not extract Google Drive file ID from URL.")

        gdrive_url = f"https://drive.google.com/uc?id={file_id}"
        output_path = str(download_dir / "gdrive_file")

        try:
            if on_progress:
                on_progress(0.1, DOWNLOAD_GDRIVE_IN_PROGRESS)

            result = gdown.download(gdrive_url, output_path, quiet=True, fuzzy=False)
            if result is None:
                raise DownloadError(
                    "Failed to download from Google Drive. Make sure the file is shared as 'Anyone with the link'."
                )
        except DownloadError:
            raise
        except BaseTimeoutException:
            raise
        except Exception as e:
            raise DownloadError(f"Failed to download from Google Drive: {e}") from e

        downloaded = Path(result)
        if not downloaded.exists():
            raise DownloadError("Download completed but no file was found.")

        if on_progress:
            on_progress(1.0, DOWNLOAD_DONE)

        context["input_path"] = str(downloaded)
        context["video_title"] = downloaded.stem
        return context

    def _download_youtube(
        self,
        source_url: str,
        context: dict[str, Any],
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
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
            # Defense in depth: the URL is already whitelisted and canonicalised
            # to a youtube.com/watch URL by validate_youtube_url, but pinning the
            # extractor stops yt-dlp from ever falling back to the generic
            # extractor and fetching an arbitrary (e.g. internal) host.
            "allowed_extractors": ["youtube"],
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
        except BaseTimeoutException:
            # Let RQ's death penalty propagate so the worker task classifies
            # it as a timeout instead of a download failure.
            raise
        except Exception as e:
            raise DownloadError(f"Failed to download video: {e}") from e

        downloaded_files = list(download_dir.glob("video.*"))
        if not downloaded_files:
            raise DownloadError("Download completed but no video file was found.")

        context["input_path"] = str(downloaded_files[0])
        context["video_title"] = info.get("title", "")
        return context

    def cleanup(self) -> None:
        pass
