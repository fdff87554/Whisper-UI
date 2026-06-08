"""URL validation and cleaning for YouTube and Google Drive (regex-only, no external dependencies)."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


class YouTubeURLError(ValueError):
    pass


class PlaylistURLError(YouTubeURLError):
    pass


class GoogleDriveURLError(ValueError):
    pass


_YOUTUBE_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}

_VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")

_GDRIVE_HOSTS = {"drive.google.com", "docs.google.com"}
_GDRIVE_FILE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{10,}$")


def validate_youtube_url(url: str) -> str:
    """Validate and clean a YouTube URL, returning a canonical URL with only the video ID.

    Raises YouTubeURLError for invalid URLs and PlaylistURLError for playlist URLs.
    """
    url = url.strip()
    if not url:
        raise YouTubeURLError("URL is empty.")

    parsed = urlparse(url)

    if not parsed.scheme:
        parsed = urlparse(f"https://{url}")

    if parsed.scheme not in ("http", "https"):
        raise YouTubeURLError("Invalid URL scheme.")

    host = parsed.hostname or ""
    if host not in _YOUTUBE_HOSTS:
        raise YouTubeURLError("Not a YouTube URL.")

    qs = parse_qs(parsed.query)

    # Reject playlist-only URLs
    if parsed.path == "/playlist" or (qs.get("list") and not qs.get("v")):
        raise PlaylistURLError("Playlist URLs are not supported.")

    video_id = _extract_video_id(host, parsed.path, qs)
    if not video_id or not _VIDEO_ID_RE.match(video_id):
        raise YouTubeURLError("Could not extract a valid video ID.")

    # Return canonical clean URL
    clean_qs = urlencode({"v": video_id})
    return urlunparse(("https", "www.youtube.com", "/watch", "", clean_qs, ""))


def _extract_video_id(host: str, path: str, qs: dict[str, list[str]]) -> str | None:
    """Extract video ID from different YouTube URL formats."""
    # youtu.be/<id>
    if host in ("youtu.be", "www.youtu.be"):
        return path.lstrip("/").split("/")[0] if path.strip("/") else None

    # youtube.com/shorts/<id>
    if path.startswith("/shorts/"):
        parts = path.split("/")
        return parts[2] if len(parts) > 2 else None

    # youtube.com/watch?v=<id>
    v_list = qs.get("v")
    if v_list:
        return v_list[0]

    # youtube.com/embed/<id>
    if path.startswith("/embed/"):
        parts = path.split("/")
        return parts[2] if len(parts) > 2 else None

    return None


def validate_google_drive_url(url: str) -> str:
    """Validate a Google Drive sharing URL and return a canonical download URL.

    Accepts formats like:
      - https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing
      - https://drive.google.com/open?id={FILE_ID}
      - https://drive.google.com/uc?id={FILE_ID}&export=download

    Raises GoogleDriveURLError for invalid URLs.
    Returns a canonical URL: https://drive.google.com/uc?export=download&id={FILE_ID}
    """
    url = url.strip()
    if not url:
        raise GoogleDriveURLError("URL is empty.")

    parsed = urlparse(url)

    if not parsed.scheme:
        parsed = urlparse(f"https://{url}")

    if parsed.scheme not in ("http", "https"):
        raise GoogleDriveURLError("Invalid URL scheme.")

    host = parsed.hostname or ""
    if host not in _GDRIVE_HOSTS:
        raise GoogleDriveURLError("Not a Google Drive URL.")

    file_id = _extract_gdrive_file_id(parsed.path, parse_qs(parsed.query))
    if not file_id or not _GDRIVE_FILE_ID_RE.match(file_id):
        raise GoogleDriveURLError("Could not extract a valid file ID.")

    clean_qs = urlencode({"export": "download", "id": file_id})
    return urlunparse(("https", "drive.google.com", "/uc", "", clean_qs, ""))


def _extract_gdrive_file_id(path: str, qs: dict[str, list[str]]) -> str | None:
    # /file/d/{FILE_ID}/...
    if "/d/" in path:
        parts = path.split("/d/")
        if len(parts) > 1:
            file_id = parts[1].split("/")[0]
            if file_id:
                return file_id

    # ?id={FILE_ID}
    id_list = qs.get("id")
    if id_list:
        return id_list[0]

    return None


def is_google_drive_url(url: str) -> bool:
    """Quick check whether a URL looks like a Google Drive link."""
    try:
        parsed = urlparse(url.strip())
        host = parsed.hostname or ""
        return host in _GDRIVE_HOSTS
    except Exception:
        return False
