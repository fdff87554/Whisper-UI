from __future__ import annotations

import pytest

from whisper_ui.core.config import Settings
from whisper_ui.worker.timeout import calculate_job_timeout


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file="/dev/null",
        database_path=tmp_path / "test.db",
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
        job_timeout_default=7200,
        job_timeout_floor=1800,
        job_timeout_audio_multiplier=3.0,
        job_timeout_max=28800,
    )


def test_none_duration_returns_default(settings):
    assert calculate_job_timeout(None, settings) == 7200


def test_zero_duration_returns_default(settings):
    assert calculate_job_timeout(0, settings) == 7200


def test_negative_duration_returns_default(settings):
    assert calculate_job_timeout(-1, settings) == 7200


def test_short_audio_is_clamped_to_floor(settings):
    # 10 min audio * 3 = 1800s, right at floor
    assert calculate_job_timeout(600, settings) == 1800


def test_tiny_audio_below_floor_gets_floor(settings):
    # 1 min audio * 3 = 180s, well below floor of 1800
    assert calculate_job_timeout(60, settings) == 1800


def test_typical_audio_uses_multiplier(settings):
    # 1h audio * 3 = 10800s (3h), within [floor, max]
    assert calculate_job_timeout(3600, settings) == 10800


def test_long_audio_clamped_to_max(settings):
    # 4h audio * 3 = 43200s (12h), clamped down to 28800 (8h)
    assert calculate_job_timeout(14400, settings) == 28800


def test_custom_multiplier_applies(tmp_path):
    s = Settings(
        _env_file="/dev/null",
        database_path=tmp_path / "test.db",
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
        job_timeout_floor=600,
        job_timeout_max=36000,
        job_timeout_audio_multiplier=2.0,
    )
    assert calculate_job_timeout(3600, s) == 7200


def test_return_type_is_int(settings):
    result = calculate_job_timeout(1234.5, settings)
    assert isinstance(result, int)
