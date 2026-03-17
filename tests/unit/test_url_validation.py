from __future__ import annotations

import pytest

from whisper_ui.web.url_validation import PlaylistURLError, YouTubeURLError, validate_youtube_url


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
