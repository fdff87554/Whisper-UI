from __future__ import annotations

import pytest

from whisper_ui.pipeline.preprocess import PreprocessStage


@pytest.mark.skipif(True, reason="Integration tests require FFmpeg and GPU resources")
class TestPipelineIntegration:
    def test_preprocess_wav(self, tmp_path):
        stage = PreprocessStage()
        wav_path = tmp_path / "test.wav"
        wav_path.write_bytes(b"RIFF" + b"\x00" * 100)
        context = {"input_path": str(wav_path)}
        stage.execute(context)
