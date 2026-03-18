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
