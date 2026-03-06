from __future__ import annotations

from whisper_ui.core.config import Settings


def test_default_settings(tmp_path):
    s = Settings(
        database_path=tmp_path / "test.db",
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
    )
    assert s.whisper_model == "large-v3"
    assert s.compute_type == "int8_float16"
    assert s.device == "cuda"
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
