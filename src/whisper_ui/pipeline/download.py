from __future__ import annotations

import logging
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
    DOWNLOAD_TWITTER_RESTRICTED,
)
from whisper_ui.web.url_validation import extract_gdrive_file_id, is_google_drive_url, is_twitter_url

if TYPE_CHECKING:
    from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)

# Substrings (lowercase) that mark an X download failure the user can act on:
# login walls, age/NSFW gating, protected accounts, or media that needs a
# different (unsupported) extractor such as Broadcasts / Spaces. The
# "no suitable extractor" / "broadcast" markers fire when the ["twitter"] pin
# blocks yt-dlp from re-extracting into a twitter:broadcast / twitter:spaces
# sub-extractor (verified against yt-dlp 2026.03.17 on a real Broadcast tweet).
# Deliberately NOT included: bare "unavailable" (matches the transient
# "HTTP Error 503: Service Unavailable") and HTTP 5xx/429 — those are retryable
# and must keep the generic "Failed to download" path, not the cookies hint.
_TWITTER_RESTRICTED_MARKERS = (
    "log in",
    "login",
    "authenticate",
    "nsfw",
    "not authorized",
    "private",
    "unsupported url",
    "no suitable extractor",
    "broadcast",
)


class DownloadStage:
    def __init__(self, *, max_duration: int = 14400, twitter_cookies_file: str | None = None) -> None:
        self._max_duration = max_duration
        self._twitter_cookies_file = twitter_cookies_file

    @property
    def name(self) -> str:
        return "download"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        source_url = context.get("source_url")
        if not source_url:
            return context

        if is_google_drive_url(source_url):
            return self._download_google_drive(source_url, context, on_progress)
        if is_twitter_url(source_url):
            return self._download_via_ytdlp(
                source_url,
                context,
                on_progress,
                allowed_extractors=["twitter"],
                cookiefile=self._cookiefile_if_present(),
            )
        return self._download_via_ytdlp(source_url, context, on_progress, allowed_extractors=["youtube"])

    def _cookiefile_if_present(self) -> str | None:
        """Return the configured Twitter cookies file only when it exists.

        Unset, or a path that does not point at a real file, degrades to None so
        the download falls back to an anonymous attempt instead of crashing.
        """
        path = self._twitter_cookies_file
        if path and Path(path).is_file():
            return path
        return None

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

        parsed = urlparse(source_url)
        file_id = extract_gdrive_file_id(parsed.path, parse_qs(parsed.query))
        if not file_id:
            raise DownloadError("Could not extract Google Drive file ID from URL.")

        gdrive_url = f"https://drive.google.com/uc?id={file_id}"
        output_path = str(download_dir) + "/"

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
        if not downloaded.is_file() or downloaded.stat().st_size == 0:
            raise DownloadError("Download completed but the file was empty or not found.")

        from whisper_ui.pipeline.preprocess import SUPPORTED_EXTENSIONS

        if downloaded.suffix.lower() not in SUPPORTED_EXTENSIONS:
            downloaded.unlink(missing_ok=True)
            raise DownloadError(f"Downloaded file '{downloaded.name}' is not a supported audio or video format.")

        if on_progress:
            on_progress(1.0, DOWNLOAD_DONE)

        context["input_path"] = str(downloaded)
        context["video_title"] = downloaded.stem
        return context

    def _download_via_ytdlp(
        self,
        source_url: str,
        context: dict[str, Any],
        on_progress: ProgressCallback | None,
        *,
        allowed_extractors: list[str],
        cookiefile: str | None = None,
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
            # by the validate_*_url helper, but pinning the extractor stops
            # yt-dlp from ever falling back to the generic extractor and
            # fetching an arbitrary (e.g. internal) host.
            "allowed_extractors": allowed_extractors,
            "socket_timeout": YT_DLP_SOCKET_TIMEOUT,
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
        }
        # Operator-supplied login cookies (X login-walled / age-restricted posts).
        # Only set when the file actually exists, so an unset/missing path stays
        # an anonymous attempt rather than failing the download.
        if cookiefile:
            ydl_opts["cookiefile"] = cookiefile

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
            if "twitter" in allowed_extractors and any(m in str(e).lower() for m in _TWITTER_RESTRICTED_MARKERS):
                raise DownloadError(DOWNLOAD_TWITTER_RESTRICTED) from e
            raise DownloadError(f"Failed to download video: {e}") from e

        downloaded_files = list(download_dir.glob("video.*"))
        if not downloaded_files:
            raise DownloadError("Download completed but no video file was found.")

        context["input_path"] = str(downloaded_files[0])
        context["video_title"] = info.get("title", "")
        return context

    def cleanup(self) -> None:
        pass
