from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from whisper_ui.core.device import detect_device, release_gpu_memory, validate_compute_type


class TestDetectDevice:
    def test_auto_with_cuda(self):
        with patch("whisper_ui.core.device._cuda_available", return_value=True):
            assert detect_device("auto") == "cuda"

    def test_auto_without_cuda(self):
        with patch("whisper_ui.core.device._cuda_available", return_value=False):
            assert detect_device("auto") == "cpu"

    def test_auto_default_arg(self):
        with patch("whisper_ui.core.device._cuda_available", return_value=False):
            assert detect_device() == "cpu"

    def test_explicit_cpu(self):
        assert detect_device("cpu") == "cpu"

    def test_explicit_cuda_available(self):
        with patch("whisper_ui.core.device._cuda_available", return_value=True):
            assert detect_device("cuda") == "cuda"

    def test_explicit_cuda_unavailable(self, caplog):
        with patch("whisper_ui.core.device._cuda_available", return_value=False), caplog.at_level(logging.WARNING):
            result = detect_device("cuda")
        assert result == "cpu"
        assert "Falling back to CPU" in caplog.text

    def test_unsupported_device(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = detect_device("mps")
        assert result == "cpu"
        assert "Unsupported device" in caplog.text


class TestValidateComputeType:
    def test_cpu_float16_downgrades(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = validate_compute_type("cpu", "float16")
        assert result == "int8"
        assert "not supported on CPU" in caplog.text

    def test_cpu_int8_float16_downgrades(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = validate_compute_type("cpu", "int8_float16")
        assert result == "int8"

    def test_cpu_int8_unchanged(self):
        assert validate_compute_type("cpu", "int8") == "int8"

    def test_cuda_float16_unchanged(self):
        assert validate_compute_type("cuda", "float16") == "float16"

    def test_cuda_int8_float16_unchanged(self):
        assert validate_compute_type("cuda", "int8_float16") == "int8_float16"


class TestReleaseGpuMemory:
    def test_with_cuda(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"torch": mock_torch}):
            release_gpu_memory()
        mock_torch.cuda.empty_cache.assert_called_once()

    def test_without_cuda(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"torch": mock_torch}):
            release_gpu_memory()
        mock_torch.cuda.empty_cache.assert_not_called()

    def test_no_torch(self):
        with patch.dict("sys.modules", {"torch": None}):
            # Should not raise
            release_gpu_memory()
