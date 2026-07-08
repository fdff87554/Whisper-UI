"""URL validation and cleaning for YouTube, Google Drive, and Twitter/X (regex-only, no external dependencies)."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


class YouTubeURLError(ValueError):
    pass


class PlaylistURLError(YouTubeURLError):
    pass


class UnsupportedPlaylistTypeError(YouTubeURLError):
    pass


class GoogleDriveURLError(ValueError):
    pass


class TwitterURLError(ValueError):
    pass


_YOUTUBE_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}

_VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")

# Public, enumerable playlist IDs (user playlists PL*, channel uploads UU*,
# albums OLAK5uy_*, favourites FL*). The 13-42 length window covers all of
# them while excluding the 2-char login-bound IDs below.
_PLAYLIST_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{13,42}$")
# RD*/UL* are auto-generated Mixes (endless, per-viewer); WL/LL/LM are Watch
# Later / Liked videos / Liked music, which require a signed-in account. None
# of them can be enumerated by an anonymous yt-dlp extraction.
_UNSUPPORTED_PLAYLIST_PREFIXES = ("RD", "UL")
_UNSUPPORTED_PLAYLIST_IDS = frozenset({"WL", "LL", "LM"})

_GDRIVE_HOSTS = {"drive.google.com", "docs.google.com"}
_GDRIVE_FILE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{10,}$")

_TWITTER_HOSTS = {
    "x.com",
    "www.x.com",
    "m.x.com",
    "mobile.x.com",
    "twitter.com",
    "www.twitter.com",
    "m.twitter.com",
    "mobile.twitter.com",
}
# Numeric snowflake post id (currently ~19 digits); upper bound stays generous
# while still rejecting any non-numeric segment. ASCII-only ([0-9], not \d) so
# unicode digits cannot slip into the canonical URL.
_TWEET_ID_RE = re.compile(r"^[0-9]{1,25}$")


def _parse_with_scheme(url: str, error_cls: type[ValueError]):
    """Parse a URL, prepending https:// if no scheme is present.

    ``urlparse`` raises a bare ``ValueError`` for a handful of malformed inputs
    — most notably an unmatched ``[`` ("Invalid IPv6 URL"), which is what a user
    pasting a Markdown link like ``[title](https://...)`` produces. Wrap it in
    the caller's domain error so a ``validate_*`` call always fails with a
    YouTube/GoogleDrive/Twitter error (which the routes catch and count as an
    invalid line) instead of a bare ValueError that escapes to a generic 500.
    """
    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            parsed = urlparse(f"https://{url}")
    except ValueError as exc:
        raise error_cls("Malformed URL.") from exc
    return parsed


def validate_youtube_url(url: str) -> str:
    """Validate and clean a YouTube URL, returning a canonical URL with only the video ID.

    Raises YouTubeURLError for invalid URLs and PlaylistURLError for playlist URLs.
    """
    url = url.strip()
    if not url:
        raise YouTubeURLError("URL is empty.")

    parsed = _parse_with_scheme(url, YouTubeURLError)

    if parsed.scheme not in ("http", "https"):
        raise YouTubeURLError("Invalid URL scheme.")

    host = parsed.hostname or ""
    if host not in _YOUTUBE_HOSTS:
        raise YouTubeURLError("Not a YouTube URL.")

    qs = parse_qs(parsed.query)

    # Reject playlist-only URLs. A link that names a specific video (watch?v=,
    # youtu.be/<id>, shorts, embed) resolves to that single video even when a
    # list= parameter tags along, matching the share links YouTube emits while
    # a playlist is playing.
    if parsed.path == "/playlist" or (qs.get("list") and not _extract_video_id(host, parsed.path, qs)):
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


def validate_youtube_playlist_url(url: str) -> str:
    """Validate a YouTube playlist URL and return a canonical playlist URL.

    Accepts /playlist?list={ID} links and watch links whose only content
    reference is a list= parameter. Raises UnsupportedPlaylistTypeError for
    auto-generated or login-bound lists (Mixes, Watch Later, Liked videos)
    and YouTubeURLError for anything else that is not a valid playlist link.

    Returns a canonical URL: https://www.youtube.com/playlist?list={ID}
    """
    url = url.strip()
    if not url:
        raise YouTubeURLError("URL is empty.")

    parsed = _parse_with_scheme(url, YouTubeURLError)

    if parsed.scheme not in ("http", "https"):
        raise YouTubeURLError("Invalid URL scheme.")

    host = parsed.hostname or ""
    if host not in _YOUTUBE_HOSTS:
        raise YouTubeURLError("Not a YouTube URL.")

    list_ids = parse_qs(parsed.query).get("list")
    if not list_ids:
        raise YouTubeURLError("Could not extract a playlist ID.")

    playlist_id = list_ids[0]
    if playlist_id in _UNSUPPORTED_PLAYLIST_IDS or playlist_id.startswith(_UNSUPPORTED_PLAYLIST_PREFIXES):
        raise UnsupportedPlaylistTypeError("Auto-generated and login-bound playlists are not supported.")
    if not _PLAYLIST_ID_RE.match(playlist_id):
        raise YouTubeURLError("Could not extract a valid playlist ID.")

    clean_qs = urlencode({"list": playlist_id})
    return urlunparse(("https", "www.youtube.com", "/playlist", "", clean_qs, ""))


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

    parsed = _parse_with_scheme(url, GoogleDriveURLError)

    if parsed.scheme not in ("http", "https"):
        raise GoogleDriveURLError("Invalid URL scheme.")

    host = parsed.hostname or ""
    if host not in _GDRIVE_HOSTS:
        raise GoogleDriveURLError("Not a Google Drive URL.")

    if host == "docs.google.com" and any(
        p in parsed.path for p in ("/document/", "/spreadsheets/", "/presentation/", "/forms/")
    ):
        raise GoogleDriveURLError(
            "Google Docs, Sheets, Slides, and Forms are not supported. "
            "Please provide a sharing link to an audio or video file stored in Google Drive."
        )

    file_id = extract_gdrive_file_id(parsed.path, parse_qs(parsed.query))
    if not file_id or not _GDRIVE_FILE_ID_RE.match(file_id):
        raise GoogleDriveURLError("Could not extract a valid file ID.")

    clean_qs = urlencode({"export": "download", "id": file_id})
    return urlunparse(("https", "drive.google.com", "/uc", "", clean_qs, ""))


def extract_gdrive_file_id(path: str, qs: dict[str, list[str]]) -> str | None:
    """Extract Google Drive file ID from URL path and query string."""
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


def is_valid_gdrive_file_id(file_id: str) -> bool:
    """Return True when *file_id* matches the canonical Drive file-ID shape.

    Lets a consumer re-check an id it extracted itself (e.g. the worker
    download stage) without depending on ``validate_google_drive_url`` having
    already run, keeping that boundary self-validating.
    """
    return bool(_GDRIVE_FILE_ID_RE.match(file_id))


def validate_twitter_url(url: str) -> str:
    """Validate a Twitter/X status URL and return a canonical URL.

    Accepts a link to a single post, e.g.:
      - https://x.com/{user}/status/{id}
      - https://twitter.com/{user}/status/{id}
      - https://mobile.twitter.com/{user}/status/{id}
      - https://x.com/i/status/{id}

    Raises TwitterURLError for non-post URLs (profiles, threads, Spaces).
    Returns a canonical URL: https://x.com/i/status/{id} (handle and any
    tracking query params such as ?s=20 are dropped).
    """
    url = url.strip()
    if not url:
        raise TwitterURLError("URL is empty.")

    parsed = _parse_with_scheme(url, TwitterURLError)

    if parsed.scheme not in ("http", "https"):
        raise TwitterURLError("Invalid URL scheme.")

    host = parsed.hostname or ""
    if host not in _TWITTER_HOSTS:
        raise TwitterURLError("Not a Twitter/X URL.")

    tweet_id = _extract_tweet_id(parsed.path)
    if not tweet_id or not _TWEET_ID_RE.match(tweet_id):
        raise TwitterURLError(
            "Could not extract a post ID. Provide a link to a single post "
            "(e.g. https://x.com/user/status/123...), not a profile or Space."
        )

    return urlunparse(("https", "x.com", f"/i/status/{tweet_id}", "", "", ""))


def _extract_tweet_id(path: str) -> str | None:
    """Extract the numeric post ID from /{user}/status/{id} or /i/status/{id}."""
    parts = [p for p in path.split("/") if p]
    # The id is the numeric segment following a 'status' (legacy: 'statuses')
    # segment. Require the next segment to be numeric so a handle literally
    # named "status" (e.g. /status/status/20) does not shadow the real id.
    for i, seg in enumerate(parts):
        if seg in ("status", "statuses") and i + 1 < len(parts) and parts[i + 1].isdigit():
            return parts[i + 1]
    return None


def is_youtube_playlist_url(url: str) -> bool:
    """Quick check whether a URL is a playlist-only YouTube link.

    True only when no specific video is identifiable (the /playlist path, or a
    list= parameter without a video ID); mirrors the rejection condition in
    validate_youtube_url so routing and validation can never disagree.
    """
    try:
        parsed = _parse_with_scheme(url.strip(), YouTubeURLError)
    except Exception:
        return False
    host = parsed.hostname or ""
    if host not in _YOUTUBE_HOSTS:
        return False
    if parsed.path == "/playlist":
        return True
    qs = parse_qs(parsed.query)
    return bool(qs.get("list")) and not _extract_video_id(host, parsed.path, qs)


def is_google_drive_url(url: str) -> bool:
    """Quick check whether a URL looks like a Google Drive link."""
    try:
        parsed = _parse_with_scheme(url.strip(), GoogleDriveURLError)
        host = parsed.hostname or ""
        return host in _GDRIVE_HOSTS
    except Exception:
        return False


def is_twitter_url(url: str) -> bool:
    """Quick check whether a URL looks like a Twitter/X link."""
    try:
        parsed = _parse_with_scheme(url.strip(), TwitterURLError)
        host = parsed.hostname or ""
        return host in _TWITTER_HOSTS
    except Exception:
        return False
