from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from whisper_ui.core.exceptions import DownloadError
from whisper_ui.pipeline.download import DownloadStage


class TestDownloadStageNoOp:
    def test_no_source_url_passes_through(self):
        stage = DownloadStage()
        context: dict[str, Any] = {"input_path": "/some/file.mp3"}
        result = stage.execute(context)
        assert result["input_path"] == "/some/file.mp3"

    def test_empty_source_url_passes_through(self):
        stage = DownloadStage()
        context: dict[str, Any] = {"source_url": "", "input_path": "/some/file.mp3"}
        result = stage.execute(context)
        assert result["input_path"] == "/some/file.mp3"


class TestDownloadStageWithMock:
    @pytest.fixture
    def download_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "downloads"
        d.mkdir()
        return d

    @pytest.fixture
    def context(self, download_dir: Path) -> dict[str, Any]:
        return {
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "download_dir": str(download_dir),
            "input_path": "",
        }

    def _make_mock_ydl(self, download_dir: Path, duration: int = 120, title: str = "Test Video"):
        """Create a mock YoutubeDL that simulates a successful download."""
        mock_ydl_instance = MagicMock()

        def extract_info(url, download=True):
            info = {"duration": duration, "title": title}
            if download:
                # Simulate file creation
                (Path(download_dir) / "video.mp4").write_bytes(b"fake video")
            return info

        mock_ydl_instance.extract_info = extract_info
        mock_ydl_instance.__enter__ = lambda self: self
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)
        return mock_ydl_instance

    def test_successful_download(self, context, download_dir):
        mock_ydl = self._make_mock_ydl(download_dir)
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = mock_ydl

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            stage = DownloadStage()
            result = stage.execute(context)

        assert result["input_path"] == str(download_dir / "video.mp4")
        assert result["video_title"] == "Test Video"

    def test_progress_callback_called(self, context, download_dir):
        mock_ydl = self._make_mock_ydl(download_dir)
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = mock_ydl

        progress_log: list[tuple[float, str]] = []

        def on_progress(p: float, msg: str) -> None:
            progress_log.append((p, msg))

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            stage = DownloadStage()
            stage.execute(context, on_progress=on_progress)

        assert len(progress_log) > 0
        assert progress_log[0][1] == "正在取得影片資訊..."

    def test_duration_exceeds_limit(self, context, download_dir):
        mock_ydl = self._make_mock_ydl(download_dir, duration=50000)
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = mock_ydl

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            stage = DownloadStage(max_duration=3600)
            with pytest.raises(DownloadError, match="exceeds the maximum"):
                stage.execute(context)

    def test_extract_info_returns_none(self, context, download_dir):
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.return_value = None
        mock_ydl_instance.__enter__ = lambda self: self
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)

        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = mock_ydl_instance

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            stage = DownloadStage()
            with pytest.raises(DownloadError, match="Failed to extract"):
                stage.execute(context)

    def test_network_error_raises_download_error(self, context, download_dir):
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.side_effect = Exception("Network unreachable")
        mock_ydl_instance.__enter__ = lambda self: self
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)

        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = mock_ydl_instance

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            stage = DownloadStage()
            with pytest.raises(DownloadError, match="Failed to download"):
                stage.execute(context)

    def test_restricts_yt_dlp_to_youtube_extractor(self, context, download_dir):
        mock_ydl = self._make_mock_ydl(download_dir)
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = mock_ydl

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            DownloadStage().execute(context)

        ydl_opts = mock_module.YoutubeDL.call_args.args[0]
        assert ydl_opts["allowed_extractors"] == ["youtube"]

    def test_cleanup_is_noop(self):
        stage = DownloadStage()
        stage.cleanup()  # Should not raise


class TestDownloadStageImportError:
    def test_missing_yt_dlp_raises(self):
        context: dict[str, Any] = {
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "download_dir": "/tmp/test",
            "input_path": "",
        }
        with patch.dict("sys.modules", {"yt_dlp": None}):
            stage = DownloadStage()
            with pytest.raises(DownloadError, match="yt-dlp is not installed"):
                stage.execute(context)


class TestGoogleDriveDownload:
    @pytest.fixture
    def download_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "downloads"
        d.mkdir()
        return d

    @pytest.fixture
    def context(self, download_dir: Path) -> dict[str, Any]:
        return {
            "source_url": "https://drive.google.com/uc?export=download&id=1AbCdEfGhIjKlMnOpQrStUvWxYz",
            "download_dir": str(download_dir),
            "input_path": "",
        }

    def test_successful_gdrive_download(self, context, download_dir):
        resolved_file = str(download_dir / "meeting_recording.m4a")

        def mock_download(url, output, quiet=True, fuzzy=False):
            out_path = Path(output)
            if out_path.is_dir():
                out_path = out_path / "meeting_recording.m4a"
            out_path.write_bytes(b"fake audio content")
            return str(out_path)

        mock_gdown = MagicMock()
        mock_gdown.download = mock_download

        with patch.dict("sys.modules", {"gdown": mock_gdown}):
            stage = DownloadStage()
            result = stage.execute(context)

        assert result["input_path"] == resolved_file
        assert result["video_title"] == "meeting_recording"

    def test_gdrive_progress_callback(self, context, download_dir):
        def mock_download(url, output, quiet=True, fuzzy=False):
            out_path = Path(output) / "audio.mp3"
            out_path.write_bytes(b"fake audio content")
            return str(out_path)

        mock_gdown = MagicMock()
        mock_gdown.download = mock_download

        progress_log: list[tuple[float, str]] = []

        def on_progress(p: float, msg: str) -> None:
            progress_log.append((p, msg))

        with patch.dict("sys.modules", {"gdown": mock_gdown}):
            stage = DownloadStage()
            stage.execute(context, on_progress=on_progress)

        assert len(progress_log) >= 2
        assert progress_log[0][1] == "正在取得影片資訊..."
        assert progress_log[-1][1] == "音訊下載完成。"

    def test_gdrive_download_returns_none(self, context, download_dir):
        mock_gdown = MagicMock()
        mock_gdown.download.return_value = None

        with patch.dict("sys.modules", {"gdown": mock_gdown}):
            stage = DownloadStage()
            with pytest.raises(DownloadError, match="Failed to download from Google Drive"):
                stage.execute(context)

    def test_gdrive_missing_gdown_raises(self):
        context: dict[str, Any] = {
            "source_url": "https://drive.google.com/uc?export=download&id=abc123xyz",
            "download_dir": "/tmp/test",
            "input_path": "",
        }
        with patch.dict("sys.modules", {"gdown": None}):
            stage = DownloadStage()
            with pytest.raises(DownloadError, match="gdown is not installed"):
                stage.execute(context)

    def test_gdrive_invalid_file_id(self, download_dir):
        context: dict[str, Any] = {
            "source_url": "https://drive.google.com/drive/my-drive",
            "download_dir": str(download_dir),
            "input_path": "",
        }
        mock_gdown = MagicMock()

        with patch.dict("sys.modules", {"gdown": mock_gdown}):
            stage = DownloadStage()
            with pytest.raises(DownloadError, match="Could not extract Google Drive file ID"):
                stage.execute(context)

    def test_gdrive_network_error(self, context, download_dir):
        mock_gdown = MagicMock()
        mock_gdown.download.side_effect = Exception("Connection refused")

        with patch.dict("sys.modules", {"gdown": mock_gdown}):
            stage = DownloadStage()
            with pytest.raises(DownloadError, match="Failed to download from Google Drive"):
                stage.execute(context)

    def test_gdrive_empty_file_raises(self, context, download_dir):
        def mock_download(url, output, quiet=True, fuzzy=False):
            out_path = Path(output) / "audio.mp3"
            out_path.write_bytes(b"")
            return str(out_path)

        mock_gdown = MagicMock()
        mock_gdown.download = mock_download

        with patch.dict("sys.modules", {"gdown": mock_gdown}):
            stage = DownloadStage()
            with pytest.raises(DownloadError, match="empty or not found"):
                stage.execute(context)

    def test_gdrive_unsupported_extension_raises(self, context, download_dir):
        def mock_download(url, output, quiet=True, fuzzy=False):
            out_path = Path(output) / "document.txt"
            out_path.write_bytes(b"some text content")
            return str(out_path)

        mock_gdown = MagicMock()
        mock_gdown.download = mock_download

        with patch.dict("sys.modules", {"gdown": mock_gdown}):
            stage = DownloadStage()
            with pytest.raises(DownloadError, match="is not a supported audio or video format"):
                stage.execute(context)
        assert not (download_dir / "document.txt").exists()


class TestTwitterDownload:
    @pytest.fixture
    def download_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "downloads"
        d.mkdir()
        return d

    @pytest.fixture
    def context(self, download_dir: Path) -> dict[str, Any]:
        return {
            "source_url": "https://x.com/i/status/2052048266687332852",
            "download_dir": str(download_dir),
            "input_path": "",
        }

    def _make_mock_ydl(self, download_dir: Path, duration: int = 120, title: str = "X Post"):
        mock_ydl_instance = MagicMock()

        def extract_info(url, download=True):
            info = {"duration": duration, "title": title}
            if download:
                (Path(download_dir) / "video.mp4").write_bytes(b"fake video")
            return info

        mock_ydl_instance.extract_info = extract_info
        mock_ydl_instance.__enter__ = lambda self: self
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)
        return mock_ydl_instance

    def test_successful_twitter_download(self, context, download_dir):
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = self._make_mock_ydl(download_dir)

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            result = DownloadStage().execute(context)

        assert result["input_path"] == str(download_dir / "video.mp4")
        assert result["video_title"] == "X Post"

    def test_restricts_yt_dlp_to_twitter_extractor(self, context, download_dir):
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = self._make_mock_ydl(download_dir)

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            DownloadStage().execute(context)

        ydl_opts = mock_module.YoutubeDL.call_args.args[0]
        assert ydl_opts["allowed_extractors"] == ["twitter"]

    def test_cookiefile_passed_when_file_exists(self, context, download_dir, tmp_path):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# Netscape HTTP Cookie File\n")
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = self._make_mock_ydl(download_dir)

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            DownloadStage(twitter_cookies_file=str(cookies)).execute(context)

        ydl_opts = mock_module.YoutubeDL.call_args.args[0]
        assert ydl_opts["cookiefile"] == str(cookies)

    def test_cookiefile_omitted_when_unset(self, context, download_dir):
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = self._make_mock_ydl(download_dir)

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            DownloadStage().execute(context)

        ydl_opts = mock_module.YoutubeDL.call_args.args[0]
        assert "cookiefile" not in ydl_opts

    def test_cookiefile_omitted_when_file_missing(self, context, download_dir):
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = self._make_mock_ydl(download_dir)

        with patch.dict("sys.modules", {"yt_dlp": mock_module}):
            DownloadStage(twitter_cookies_file="/nonexistent/cookies.txt").execute(context)

        ydl_opts = mock_module.YoutubeDL.call_args.args[0]
        assert "cookiefile" not in ydl_opts

    def test_restricted_post_raises_actionable_error(self, context, download_dir):
        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.side_effect = Exception(
            "Sorry, you are not authorized to view this tweet. Log in."
        )
        mock_ydl_instance.__enter__ = lambda self: self
        mock_ydl_instance.__exit__ = MagicMock(return_value=False)
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = mock_ydl_instance

        with patch.dict("sys.modules", {"yt_dlp": mock_module}), pytest.raises(DownloadError, match="無法下載此貼文"):
            DownloadStage().execute(context)

    def test_duration_exceeds_limit(self, context, download_dir):
        mock_module = MagicMock()
        mock_module.YoutubeDL.return_value = self._make_mock_ydl(download_dir, duration=50000)

        with (
            patch.dict("sys.modules", {"yt_dlp": mock_module}),
            pytest.raises(DownloadError, match="exceeds the maximum"),
        ):
            DownloadStage(max_duration=3600).execute(context)
