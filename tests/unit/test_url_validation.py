from __future__ import annotations

import pytest

from whisper_ui.core.url_validation import (
    GoogleDriveURLError,
    PlaylistURLError,
    TwitterURLError,
    UnsupportedPlaylistTypeError,
    YouTubeURLError,
    is_google_drive_url,
    is_twitter_url,
    is_youtube_playlist_url,
    validate_google_drive_url,
    validate_twitter_url,
    validate_youtube_playlist_url,
    validate_youtube_url,
)


class TestValidYouTubeURLs:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtube.com/watch?v=dQw4w9WgXcQ",
            "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "www.youtube.com/watch?v=dQw4w9WgXcQ",
        ],
    )
    def test_valid_urls(self, url):
        result = validate_youtube_url(url)
        assert "v=dQw4w9WgXcQ" in result
        assert result.startswith("https://www.youtube.com/watch?v=")

    def test_strips_whitespace(self):
        result = validate_youtube_url("  https://youtu.be/dQw4w9WgXcQ  ")
        assert "v=dQw4w9WgXcQ" in result

    def test_removes_extra_params(self):
        result = validate_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxyz&index=3&t=120")
        assert result == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class TestInvalidYouTubeURLs:
    @pytest.mark.parametrize(
        "url",
        [
            "",
            "not a url",
            "https://example.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/",
            "https://www.youtube.com/watch",
            "https://www.youtube.com/watch?v=short",
            "ftp://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ],
    )
    def test_invalid_urls_raise(self, url):
        with pytest.raises(YouTubeURLError):
            validate_youtube_url(url)


class TestPlaylistURLs:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
            "https://www.youtube.com/watch?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
        ],
    )
    def test_playlist_urls_raise(self, url):
        with pytest.raises(PlaylistURLError):
            validate_youtube_url(url)

    def test_video_with_list_param_accepted(self):
        """A URL with both v= and list= should be accepted (single video from playlist)."""
        result = validate_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxyz")
        assert "v=dQw4w9WgXcQ" in result

    def test_short_link_with_list_param_accepted(self):
        """A youtu.be share link carrying a list= parameter still names a video."""
        result = validate_youtube_url("https://youtu.be/dQw4w9WgXcQ?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf")
        assert result == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class TestIsYouTubePlaylistURL:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
            "https://m.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
            "https://www.youtube.com/watch?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
            "www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
            "https://youtu.be/?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
        ],
    )
    def test_playlist_only_urls_detected(self, url):
        assert is_youtube_playlist_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxyz",
            "https://youtu.be/dQw4w9WgXcQ?list=PLxyz",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ?list=PLxyz",
            "https://drive.google.com/file/d/abc123/view",
            "https://example.com/playlist?list=PLxyz",
            "",
        ],
    )
    def test_video_and_foreign_urls_not_detected(self, url):
        assert is_youtube_playlist_url(url) is False


class TestValidateYouTubePlaylistURL:
    _PLAYLIST_ID = "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/playlist?list={pid}",
            "https://youtube.com/playlist?list={pid}",
            "https://m.youtube.com/playlist?list={pid}",
            "www.youtube.com/playlist?list={pid}",
            "https://www.youtube.com/playlist?list={pid}&si=tracking",
            "https://www.youtube.com/watch?list={pid}",
        ],
    )
    def test_valid_urls_canonicalize(self, url):
        result = validate_youtube_playlist_url(url.format(pid=self._PLAYLIST_ID))
        assert result == f"https://www.youtube.com/playlist?list={self._PLAYLIST_ID}"

    def test_strips_whitespace(self):
        result = validate_youtube_playlist_url(f"  https://www.youtube.com/playlist?list={self._PLAYLIST_ID}  ")
        assert result == f"https://www.youtube.com/playlist?list={self._PLAYLIST_ID}"

    @pytest.mark.parametrize(
        "playlist_id",
        [
            "WL",  # Watch Later (login-bound)
            "LL",  # Liked videos (login-bound)
            "LM",  # Liked music (login-bound)
            "RDdQw4w9WgXcQ",  # Mix/Radio (auto-generated, endless)
            "RDMMdQw4w9WgXcQ",
            "ULdQw4w9WgXcQabcdefg",
        ],
    )
    def test_unsupported_playlist_types_raise(self, playlist_id):
        with pytest.raises(UnsupportedPlaylistTypeError):
            validate_youtube_playlist_url(f"https://www.youtube.com/playlist?list={playlist_id}")

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "https://www.youtube.com/playlist",
            "https://www.youtube.com/playlist?list=",
            "https://www.youtube.com/playlist?list=short",
            "https://www.youtube.com/playlist?list=PL%24%24invalid%24chars%24%24",
            "https://example.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
            "ftp://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf",
        ],
    )
    def test_invalid_urls_raise(self, url):
        with pytest.raises(YouTubeURLError):
            validate_youtube_playlist_url(url)


class TestValidGoogleDriveURLs:
    @pytest.mark.parametrize(
        "url,expected_id",
        [
            (
                "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view?usp=sharing",
                "1AbCdEfGhIjKlMnOpQrStUvWxYz",
            ),
            (
                "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view",
                "1AbCdEfGhIjKlMnOpQrStUvWxYz",
            ),
            (
                "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUvWxYz",
                "1AbCdEfGhIjKlMnOpQrStUvWxYz",
            ),
            (
                "https://drive.google.com/uc?id=1AbCdEfGhIjKlMnOpQrStUvWxYz&export=download",
                "1AbCdEfGhIjKlMnOpQrStUvWxYz",
            ),
        ],
    )
    def test_valid_urls(self, url, expected_id):
        result = validate_google_drive_url(url)
        assert f"id={expected_id}" in result
        assert result.startswith("https://drive.google.com/uc?")

    def test_strips_whitespace(self):
        result = validate_google_drive_url("  https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view  ")
        assert "id=1AbCdEfGhIjKlMnOpQrStUvWxYz" in result

    def test_returns_canonical_download_url(self):
        result = validate_google_drive_url(
            "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view?usp=sharing"
        )
        assert "export=download" in result
        assert "id=1AbCdEfGhIjKlMnOpQrStUvWxYz" in result


class TestInvalidGoogleDriveURLs:
    @pytest.mark.parametrize(
        "url",
        [
            "",
            "not a url",
            "https://example.com/file/d/abc123/view",
            "https://drive.google.com/",
            "https://drive.google.com/drive/my-drive",
            "ftp://drive.google.com/file/d/abc123/view",
            "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit",
            "https://docs.google.com/u/0/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit",
            "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit",
            "https://docs.google.com/u/1/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit",
            "https://docs.google.com/presentation/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit",
        ],
    )
    def test_invalid_urls_raise(self, url):
        with pytest.raises(GoogleDriveURLError):
            validate_google_drive_url(url)


class TestIsGoogleDriveURL:
    def test_google_drive_url(self):
        assert is_google_drive_url("https://drive.google.com/file/d/abc123/view") is True

    def test_google_drive_url_without_scheme(self):
        assert is_google_drive_url("drive.google.com/file/d/abc123/view") is True

    def test_youtube_url(self):
        assert is_google_drive_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is False

    def test_random_url(self):
        assert is_google_drive_url("https://example.com") is False

    def test_empty_string(self):
        assert is_google_drive_url("") is False


class TestValidTwitterURLs:
    @pytest.mark.parametrize(
        "url",
        [
            "https://x.com/jack/status/20",
            "https://www.x.com/jack/status/20",
            "https://twitter.com/jack/status/20",
            "https://www.twitter.com/jack/status/20",
            "https://mobile.twitter.com/jack/status/20",
            "https://m.twitter.com/jack/status/20",
            "https://mobile.x.com/jack/status/20",
            "https://m.x.com/jack/status/20",
            "https://x.com/i/status/20",
            "http://x.com/jack/status/20",
            "www.x.com/jack/status/20",
        ],
    )
    def test_valid_urls_canonicalize_to_i_status(self, url):
        assert validate_twitter_url(url) == "https://x.com/i/status/20"

    def test_strips_whitespace(self):
        assert validate_twitter_url("  https://x.com/jack/status/20  ") == "https://x.com/i/status/20"

    def test_drops_handle_and_tracking_params(self):
        assert validate_twitter_url("https://x.com/jack/status/20?s=20&t=abc") == "https://x.com/i/status/20"

    def test_preserves_long_numeric_id(self):
        result = validate_twitter_url("https://x.com/SpaceX/status/2057292990532481513")
        assert result == "https://x.com/i/status/2057292990532481513"

    def test_extracts_id_from_video_subpath(self):
        assert validate_twitter_url("https://x.com/A24/status/1879891361333190778/video/1") == (
            "https://x.com/i/status/1879891361333190778"
        )

    def test_handle_named_status_resolves_real_id(self):
        assert validate_twitter_url("https://x.com/status/status/20") == "https://x.com/i/status/20"


class TestInvalidTwitterURLs:
    @pytest.mark.parametrize(
        "url",
        [
            "",
            "not a url",
            "https://example.com/jack/status/20",
            "https://x.com/jack",
            "https://x.com/jack/status/",
            "https://x.com/jack/status/abc",
            "https://x.com/home",
            "https://x.com/i/spaces/1nAJELXabcEKL",
            "ftp://x.com/jack/status/20",
        ],
    )
    def test_invalid_urls_raise(self, url):
        with pytest.raises(TwitterURLError):
            validate_twitter_url(url)


class TestIsTwitterURL:
    def test_x_url(self):
        assert is_twitter_url("https://x.com/jack/status/20") is True

    def test_twitter_com_url(self):
        assert is_twitter_url("https://twitter.com/jack/status/20") is True

    def test_mobile_url_without_scheme(self):
        assert is_twitter_url("mobile.twitter.com/jack/status/20") is True

    @pytest.mark.parametrize("host", ["m.twitter.com", "mobile.x.com", "m.x.com"])
    def test_mobile_subdomains(self, host):
        assert is_twitter_url(f"https://{host}/jack/status/20") is True

    def test_youtube_url(self):
        assert is_twitter_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is False

    def test_google_drive_url(self):
        assert is_twitter_url("https://drive.google.com/file/d/abc123/view") is False

    def test_empty_string(self):
        assert is_twitter_url("") is False
