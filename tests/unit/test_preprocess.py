from __future__ import annotations

import pytest

from whisper_ui.core.exceptions import PreprocessError
from whisper_ui.pipeline.preprocess import SUPPORTED_EXTENSIONS, PreprocessStage


def test_supported_extensions():
    assert ".mp3" in SUPPORTED_EXTENSIONS
    assert ".wav" in SUPPORTED_EXTENSIONS
    assert ".m4a" in SUPPORTED_EXTENSIONS
    assert ".mp4" in SUPPORTED_EXTENSIONS


def test_unsupported_extension(tmp_path):
    fake_file = tmp_path / "test.xyz"
    fake_file.write_text("not audio")
    stage = PreprocessStage()
    with pytest.raises(PreprocessError, match="Unsupported file format"):
        stage.execute({"input_path": str(fake_file)})


def test_preprocess_stage_name():
    stage = PreprocessStage()
    assert stage.name == "preprocess"


def test_preprocess_cleanup():
    stage = PreprocessStage()
    stage.cleanup()
