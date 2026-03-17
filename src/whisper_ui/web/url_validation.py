"""YouTube URL validation and cleaning (regex-only, no yt-dlp dependency)."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


class YouTubeURLError(ValueError):
    pass


class PlaylistURLError(YouTubeURLError):
    pass


_YOUTUBE_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}

_VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")


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
