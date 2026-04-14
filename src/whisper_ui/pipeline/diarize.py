from __future__ import annotations

import contextlib
import gc
import logging
import math
import threading
import time
from typing import TYPE_CHECKING, Any

from whisper_ui.core.device import release_gpu_memory
from whisper_ui.core.exceptions import DiarizationError
from whisper_ui.core.messages import (
    DIARIZE_DONE,
    DIARIZE_LOADING,
    DIARIZE_RUNNING,
    DIARIZE_RUNNING_HEARTBEAT,
    DIARIZE_SKIPPED,
    DIARIZE_SKIPPED_DISABLED,
)

if TYPE_CHECKING:
    from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)

_DEFAULT_HEARTBEAT_INTERVAL = 30

# Heartbeat progress curve parameters. pyannote's DiarizationPipeline is a
# single blocking call, so we fake a "liveness" curve of the form
# 1 - exp(-elapsed / tau) to visibly advance the bar without ever reaching
# 1.0 (that's reserved for the real DIARIZE_DONE flush).
_HEARTBEAT_PROGRESS_START = 0.2
_HEARTBEAT_PROGRESS_CAP = 0.95
# Heuristic: diarize tends to run at roughly 1/4 of realtime on GPU, so we
# anchor tau to a quarter of audio length, clamped to a sensible band so
# short or very long clips still produce a believable curve.
_HEARTBEAT_TAU_MIN_SEC = 30
_HEARTBEAT_TAU_MAX_SEC = 600
_HEARTBEAT_TAU_AUDIO_RATIO = 0.25
_HEARTBEAT_TAU_FALLBACK_SEC = 90


def _is_rq_timeout(exc: BaseException) -> bool:
    """True when exc comes from rq's death-penalty timeout machinery."""
    try:
        from rq.timeouts import BaseTimeoutException
    except ImportError:
        return False
    return isinstance(exc, BaseTimeoutException)


class DiarizeStage:
    def __init__(
        self,
        hf_token: str = "",
        device: str = "cuda",
        *,
        enabled: bool = True,
        heartbeat_interval: int = _DEFAULT_HEARTBEAT_INTERVAL,
    ) -> None:
        self._hf_token = hf_token
        self._device = device
        self._enabled = enabled
        self._heartbeat_interval = heartbeat_interval
        self._pipeline = None

    @property
    def name(self) -> str:
        return "diarize"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        if not self._enabled:
            logger.info("Diarization disabled by user, skipping.")
            context["diarize_result"] = None
            if on_progress:
                on_progress(1.0, DIARIZE_SKIPPED_DISABLED)
            return context

        if not self._hf_token:
            logger.warning("No HF_TOKEN provided, skipping diarization.")
            context["diarize_result"] = None
            if on_progress:
                on_progress(1.0, DIARIZE_SKIPPED)
            return context

        if on_progress:
            on_progress(0.0, DIARIZE_LOADING)

        try:
            from whisperx.diarize import DiarizationPipeline

            self._pipeline = DiarizationPipeline(
                token=self._hf_token,
                device=self._device,
            )

            if on_progress:
                on_progress(0.2, DIARIZE_RUNNING)

            audio_path = context["audio_path"]
            num_speakers = context.get("num_speakers")

            kwargs: dict[str, Any] = {"audio": audio_path}
            if num_speakers is not None:
                kwargs["num_speakers"] = num_speakers

            tau = _compute_heartbeat_tau(context.get("duration"))
            with self._heartbeat(on_progress, tau):
                diarize_segments = self._pipeline(**kwargs)

            if on_progress:
                on_progress(1.0, DIARIZE_DONE)

            context["diarize_result"] = diarize_segments
            return context

        except ImportError as err:
            raise DiarizationError("whisperx is not installed.") from err
        except BaseException as e:
            # RQ's death penalty raises JobTimeoutException from a signal
            # handler. Letting it be wrapped as DiarizationError produced
            # the misleading "Diarization failed: Task exceeded maximum
            # timeout value (3600 seconds)" message. Re-raise the raw
            # timeout (and any non-Exception BaseException like SystemExit)
            # so worker.tasks.process_transcription can classify it.
            if _is_rq_timeout(e) or not isinstance(e, Exception):
                raise
            error_str = str(e)
            if "401" in error_str or "Unauthorized" in error_str:
                raise DiarizationError(
                    f"Diarization failed (authorization error): {e}. "
                    "Please verify your HF_TOKEN and accept the model agreements at: "
                    "https://huggingface.co/pyannote/speaker-diarization-3.1 and "
                    "https://huggingface.co/pyannote/segmentation-3.0"
                ) from e
            raise DiarizationError(f"Diarization failed: {e}") from e

    def cleanup(self) -> None:
        if self._pipeline is not None:
            del self._pipeline
            self._pipeline = None
            gc.collect()
            release_gpu_memory()

    @contextlib.contextmanager
    def _heartbeat(self, on_progress: ProgressCallback | None, tau: float):
        """Refresh diarization progress periodically from a daemon thread.

        pyannote's DiarizationPipeline is a single blocking call with no
        sub-progress hook, so we both (a) keep the Redis progress TTL warm
        and (b) advance the bar along an asymptotic ``1 - exp(-t/tau)``
        curve so users see visible motion. The curve never reaches
        ``_HEARTBEAT_PROGRESS_CAP``, so the real DIARIZE_DONE flush is
        still the only thing that completes the stage. The elapsed-time
        message carries the *real* runtime so the bar position stays an
        estimate rather than a lie.

        The background thread only touches ``on_progress`` (safe: the main
        thread is blocked inside the C++ inference call while it runs).
        It is torn down via an Event on context exit so no race can
        outlive the stage.
        """
        if on_progress is None or self._heartbeat_interval <= 0:
            yield
            return

        stop_event = threading.Event()
        start_time = time.monotonic()

        def _beat() -> None:
            while not stop_event.wait(self._heartbeat_interval):
                elapsed = time.monotonic() - start_time
                progress = _heartbeat_progress(elapsed, tau)
                try:
                    on_progress(progress, DIARIZE_RUNNING_HEARTBEAT.format(elapsed=int(elapsed)))
                except Exception:
                    logger.exception("Diarization heartbeat reporting failed")

        thread = threading.Thread(target=_beat, name="diarize-heartbeat", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=self._heartbeat_interval + 1)


def _compute_heartbeat_tau(audio_duration_seconds: float | None) -> float:
    """Pick a tau for the diarize heartbeat curve.

    When the audio length is known (populated by PreprocessStage), anchor
    tau to a quarter of realtime so a 20-minute clip settles around the
    ~63% mark after ~5 minutes. Clamp into
    ``[_HEARTBEAT_TAU_MIN_SEC, _HEARTBEAT_TAU_MAX_SEC]`` so pathological
    inputs don't produce a curve that either hangs on 20% or races past
    90% in the first heartbeat tick.
    """
    if audio_duration_seconds is None or audio_duration_seconds <= 0:
        return float(_HEARTBEAT_TAU_FALLBACK_SEC)
    scaled = audio_duration_seconds * _HEARTBEAT_TAU_AUDIO_RATIO
    return float(max(_HEARTBEAT_TAU_MIN_SEC, min(scaled, _HEARTBEAT_TAU_MAX_SEC)))


def _heartbeat_progress(elapsed_seconds: float, tau: float) -> float:
    """Map elapsed runtime onto the diarize stage's 0.2-0.95 progress band."""
    if tau <= 0 or elapsed_seconds < 0:
        return _HEARTBEAT_PROGRESS_START
    decay = 1.0 - math.exp(-elapsed_seconds / tau)
    band = _HEARTBEAT_PROGRESS_CAP - _HEARTBEAT_PROGRESS_START
    return _HEARTBEAT_PROGRESS_START + decay * band
