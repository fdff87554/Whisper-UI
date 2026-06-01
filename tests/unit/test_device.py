from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from whisper_ui.core.device import (
    _cuda_available,
    _rocm_available,
    configure_torch_for_rocm,
    detect_device,
    release_gpu_memory,
    torch_device_for,
    validate_compute_type,
)


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

    def test_auto_with_rocm(self):
        with (
            patch("whisper_ui.core.device._cuda_available", return_value=False),
            patch("whisper_ui.core.device._rocm_available", return_value=True),
        ):
            assert detect_device("auto") == "rocm"

    def test_auto_prefers_cuda_over_rocm(self):
        with (
            patch("whisper_ui.core.device._cuda_available", return_value=True),
            patch("whisper_ui.core.device._rocm_available", return_value=True),
        ):
            assert detect_device("auto") == "cuda"

    def test_explicit_rocm_available(self):
        with patch("whisper_ui.core.device._rocm_available", return_value=True):
            assert detect_device("rocm") == "rocm"

    def test_explicit_rocm_unavailable(self, caplog):
        with (
            patch("whisper_ui.core.device._rocm_available", return_value=False),
            caplog.at_level(logging.WARNING),
        ):
            result = detect_device("rocm")
        assert result == "cpu"
        assert "ROCm requested but not available" in caplog.text


class TestTorchDeviceFor:
    def test_cuda_maps_to_cuda(self):
        assert torch_device_for("cuda") == "cuda"

    def test_rocm_maps_to_cuda(self):
        assert torch_device_for("rocm") == "cuda"

    def test_cpu_maps_to_cpu(self):
        assert torch_device_for("cpu") == "cpu"

    def test_unknown_maps_to_cpu(self):
        assert torch_device_for("mps") == "cpu"


class TestConfigureTorchForRocm:
    def test_disables_cudnn_backend(self):
        mock_torch = MagicMock()
        with patch.dict("sys.modules", {"torch": mock_torch}):
            configure_torch_for_rocm()
        assert mock_torch.backends.cudnn.enabled is False

    def test_no_torch(self):
        with patch.dict("sys.modules", {"torch": None}):
            # Should not raise when torch is unavailable.
            configure_torch_for_rocm()


class TestAvailabilityProbes:
    @staticmethod
    def _torch(*, available: bool, hip: str | None) -> MagicMock:
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = available
        mock_torch.version.hip = hip
        return mock_torch

    def test_real_cuda_is_cuda_not_rocm(self):
        with patch.dict("sys.modules", {"torch": self._torch(available=True, hip=None)}):
            assert _cuda_available() is True
            assert _rocm_available() is False

    def test_hip_build_is_rocm_not_cuda(self):
        with patch.dict("sys.modules", {"torch": self._torch(available=True, hip="7.2.0")}):
            assert _cuda_available() is False
            assert _rocm_available() is True

    def test_neither_when_gpu_unavailable(self):
        with patch.dict("sys.modules", {"torch": self._torch(available=False, hip=None)}):
            assert _cuda_available() is False
            assert _rocm_available() is False


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
