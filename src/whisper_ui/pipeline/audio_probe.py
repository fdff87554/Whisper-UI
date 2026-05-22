"""Lightweight ffprobe-based audio duration helper.

Used both by :mod:`whisper_ui.pipeline.preprocess` (to record duration of
the converted 16kHz WAV) and by the upload routes (to size RQ
``job_timeout`` based on the source audio before enqueueing). Kept as a
module-level helper rather than a method so callers do not have to
instantiate a stage just to probe a file.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from whisper_ui.core.constants import FFPROBE_TIMEOUT

logger = logging.getLogger(__name__)


def _log_label(path: Path | str, job_id: str | None) -> str:
    """Return a short, log-safe label for the audio being probed.

    Logging the absolute path leaks the upload directory layout (and,
    indirectly, user-supplied filenames which may themselves be PII).
    Prefer the job id when the caller has one — it round-trips back to
    the DB row without exposing anything — and fall back to the file
    basename for upload-time probes that run before a job id exists.
    """
    if job_id:
        return f"job={job_id}"
    return f"file={Path(path).name}"


def get_audio_duration_seconds(path: Path | str, *, job_id: str | None = None) -> float | None:
    """Return the audio duration in seconds, or ``None`` on failure.

    Failures (ffprobe missing, parse error, timeout) are logged as warnings
    and yield ``None`` so callers can gracefully fall back to default
    behavior — probing must never block an upload or a pipeline run.

    Pass ``job_id`` whenever the caller already has one so that warnings
    can be correlated with the affected DB row from the logs alone; the
    helper never logs the absolute path so user-supplied filenames stay
    out of the log stream.
    """
    label = _log_label(path, job_id)
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # Probing should be near-instant — a timeout almost always means
        # the file is unreadable or the container is starved of CPU.
        logger.warning("ffprobe timeout while probing %s", label)
        return None
    except FileNotFoundError:
        # ffprobe binary is not installed in this image. Operational
        # consequence: upload routes fall back to JOB_TIMEOUT_DEFAULT
        # instead of the dynamic audio-duration-based timeout.
        logger.warning("ffprobe binary missing while probing %s", label)
        return None
    except OSError as exc:
        # PermissionError / IsADirectoryError / unexpected I/O errors —
        # log the exception class so the operator can tell which one
        # without us echoing the absolute path.
        logger.warning("ffprobe failed while probing %s (%s)", label, exc.__class__.__name__)
        return None

    stdout = result.stdout.strip()
    if not stdout:
        logger.warning("ffprobe returned empty duration while probing %s", label)
        return None
    try:
        value = float(stdout)
    except ValueError:
        logger.warning("ffprobe returned non-numeric duration while probing %s: %r", label, stdout)
        return None

    if value <= 0:
        return None
    return value
