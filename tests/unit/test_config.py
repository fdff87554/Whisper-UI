from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from whisper_ui.core.config import Settings, get_settings


def _make_settings(tmp_path, **overrides):
    base = {
        "_env_file": "/dev/null",
        "database_path": tmp_path / "test.db",
        "upload_dir": tmp_path / "uploads",
        "output_dir": tmp_path / "outputs",
    }
    base.update(overrides)
    return Settings(**base)


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


def test_timeout_defaults_are_consistent(tmp_path):
    s = Settings(
        _env_file="/dev/null",
        database_path=tmp_path / "test.db",
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
    )
    assert s.job_timeout_floor < s.job_timeout_default <= s.job_timeout_max
    assert s.job_timeout_audio_multiplier >= 1.0
    assert s.stale_job_timeout == s.job_timeout_max + s.stale_job_buffer
    assert s.redis_processing_expiry >= s.stale_job_timeout - s.stale_job_buffer
    assert s.diarize_heartbeat_interval > 0


def test_stale_job_timeout_tracks_max(tmp_path):
    s = _make_settings(
        tmp_path,
        job_timeout_max=10000,
        stale_job_buffer=500,
        redis_processing_expiry=10500,
    )
    assert s.stale_job_timeout == 10500


class TestTimeoutInvariantValidator:
    """Settings must reject inconsistent queue-timeout combinations at
    startup so operators get immediate feedback instead of silently
    counter-intuitive clamping behavior (see PR #34 Finding 3).
    """

    def test_accepts_custom_consistent_values(self, tmp_path):
        s = _make_settings(
            tmp_path,
            job_timeout_floor=600,
            job_timeout_default=3600,
            job_timeout_max=36000,
            job_timeout_audio_multiplier=2.5,
            stale_job_buffer=600,
            redis_processing_expiry=36600,
        )
        assert s.job_timeout_max == 36000

    def test_rejects_floor_above_default(self, tmp_path):
        with pytest.raises(ValidationError, match="job_timeout_default"):
            _make_settings(tmp_path, job_timeout_floor=5000, job_timeout_default=3000)

    def test_rejects_default_above_max(self, tmp_path):
        with pytest.raises(ValidationError, match="job_timeout_max"):
            _make_settings(
                tmp_path,
                job_timeout_default=20000,
                job_timeout_max=10000,
                redis_processing_expiry=11800,
            )

    def test_rejects_zero_floor(self, tmp_path):
        with pytest.raises(ValidationError, match="job_timeout_floor"):
            _make_settings(tmp_path, job_timeout_floor=0)

    def test_rejects_negative_floor(self, tmp_path):
        with pytest.raises(ValidationError, match="job_timeout_floor"):
            _make_settings(tmp_path, job_timeout_floor=-1)

    def test_rejects_non_positive_multiplier(self, tmp_path):
        with pytest.raises(ValidationError, match="job_timeout_audio_multiplier"):
            _make_settings(tmp_path, job_timeout_audio_multiplier=0)

    def test_rejects_negative_stale_buffer(self, tmp_path):
        with pytest.raises(ValidationError, match="stale_job_buffer"):
            _make_settings(tmp_path, stale_job_buffer=-60)

    def test_rejects_negative_heartbeat_interval(self, tmp_path):
        with pytest.raises(ValidationError, match="diarize_heartbeat_interval"):
            _make_settings(tmp_path, diarize_heartbeat_interval=-1)

    def test_rejects_redis_expiry_below_stale_timeout(self, tmp_path):
        with pytest.raises(ValidationError, match="redis_processing_expiry"):
            _make_settings(
                tmp_path,
                job_timeout_max=10000,
                stale_job_buffer=1000,
                redis_processing_expiry=5000,  # below 10000 + 1000 = 11000
            )

    def test_reviewer_scenario_now_fails_loudly(self, tmp_path):
        """Finding 3's reproducer: operator sets only JOB_TIMEOUT_MAX=60.

        Before the validator this silently clamped every computed timeout
        back up to JOB_TIMEOUT_FLOOR=1800, defeating the operator's intent.
        After the validator it must fail at startup.
        """
        with pytest.raises(ValidationError):
            _make_settings(tmp_path, job_timeout_max=60)


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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("http://ollama:11434", "http://ollama:11434"),
        ("http://ollama:11434/", "http://ollama:11434"),
        ("http://ollama:11434/api", "http://ollama:11434"),
        ("http://ollama:11434/api/", "http://ollama:11434"),
        ("http://host:11434/v1", "http://host:11434/v1"),  # other paths untouched
        ("http://api.internal:11434", "http://api.internal:11434"),  # host containing "api" untouched
    ],
)
def test_ollama_base_url_normalization(tmp_path, raw, expected):
    """Defensive normalization keeps httpx from producing /api/api/chat
    when users copy a URL that already includes the /api suffix.
    """
    s = _make_settings(tmp_path, ollama_base_url=raw)
    assert s.ollama_base_url == expected


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://ollama:bad",  # InvalidURL: bad port
        "ftp://host:11434",  # wrong scheme
        "not-a-url",  # httpx.URL parses but host is empty
        "http://",  # empty host
    ],
)
def test_ollama_base_url_rejected_when_invalid(tmp_path, bad_url):
    """Malformed URLs must fail at service startup rather than silently
    crash every opted-in job inside HttpxOllamaClient.__init__."""
    with pytest.raises(ValidationError):
        _make_settings(tmp_path, ollama_base_url=bad_url)


@pytest.mark.parametrize(
    "good_url",
    [
        "",  # empty disables the feature, must still pass
        "http://ollama:11434",
        "https://ollama.example.com",
        "http://192.168.1.20:11434",
    ],
)
def test_ollama_base_url_accepts_valid_values(tmp_path, good_url):
    s = _make_settings(tmp_path, ollama_base_url=good_url)
    # normalization may strip trailing slashes / /api, but the value parses.
    assert s.ollama_base_url == good_url


def test_diarization_available_reflects_hf_token(tmp_path):
    assert _make_settings(tmp_path, hf_token="").diarization_available is False
    assert _make_settings(tmp_path, hf_token="hf-test-not-real").diarization_available is True


def test_llm_correction_available_reflects_ollama_base_url(tmp_path):
    assert _make_settings(tmp_path, ollama_base_url="").llm_correction_available is False
    assert _make_settings(tmp_path, ollama_base_url="http://ollama:11434").llm_correction_available is True
