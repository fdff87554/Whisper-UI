from __future__ import annotations

import pytest


@pytest.mark.skipif(True, reason="Integration tests require Redis and GPU resources")
class TestWorkerIntegration:
    def test_process_transcription(self):
        pass
