from __future__ import annotations

from unittest.mock import patch

from whisper_ui.core.config import Settings, get_settings


def test_default_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("DEVICE", raising=False)
    monkeypatch.setenv("ENV_FILE", "/dev/null")
    s = Settings(
        _env_file="/dev/null",
        database_path=tmp_path / "test.db",
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
    )
    assert s.whisper_model == "large-v3"
    assert s.compute_type == "int8_float16"
    assert s.device == "auto"
    assert s.language == "zh"
    assert s.batch_size == 4


def test_settings_override(tmp_path):
    s = Settings(
        whisper_model="large-v3-turbo",
        compute_type="float16",
        device="cpu",
        language="en",
        batch_size=8,
        database_path=tmp_path / "test.db",
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
    )
    assert s.whisper_model == "large-v3-turbo"
    assert s.compute_type == "float16"
    assert s.device == "cpu"
    assert s.language == "en"
    assert s.batch_size == 8


def test_get_settings_resolves_device(tmp_path):
    get_settings.cache_clear()
    with (
        patch("whisper_ui.core.config.Settings") as MockSettings,
        patch("whisper_ui.core.device.detect_device", return_value="cpu") as mock_detect,
        patch("whisper_ui.core.device.validate_compute_type", return_value="int8") as mock_validate,
    ):
        mock_instance = Settings(
            database_path=tmp_path / "test.db",
            upload_dir=tmp_path / "uploads",
            output_dir=tmp_path / "outputs",
        )
        MockSettings.return_value = mock_instance
        result = get_settings()
        mock_detect.assert_called_once_with(mock_instance.device)
        mock_validate.assert_called_once_with("cpu", mock_instance.compute_type)
        assert result.device == "cpu"
        assert result.compute_type == "int8"
    get_settings.cache_clear()


def test_get_settings_keeps_cuda_when_available(tmp_path):
    get_settings.cache_clear()
    with (
        patch("whisper_ui.core.config.Settings") as MockSettings,
        patch("whisper_ui.core.device.detect_device", return_value="cuda"),
        patch("whisper_ui.core.device.validate_compute_type", return_value="int8_float16"),
    ):
        mock_instance = Settings(
            device="cuda",
            database_path=tmp_path / "test.db",
            upload_dir=tmp_path / "uploads",
            output_dir=tmp_path / "outputs",
        )
        MockSettings.return_value = mock_instance
        result = get_settings()
        assert result.device == "cuda"
        assert result.compute_type == "int8_float16"
    get_settings.cache_clear()
