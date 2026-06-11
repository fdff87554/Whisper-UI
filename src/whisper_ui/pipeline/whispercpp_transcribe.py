from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rq.timeouts import BaseTimeoutException

from whisper_ui.core.exceptions import TranscriptionError
from whisper_ui.core.languages import AUTO_LANGUAGE, DEFAULT_WHISPER_MODEL
from whisper_ui.core.messages import TRANSCRIBE_DONE, TRANSCRIBE_LOADING, TRANSCRIBE_RUNNING

if TYPE_CHECKING:
    from whisper_ui.pipeline.base import ProgressCallback

logger = logging.getLogger(__name__)

# whisper.cpp publishes GGML weights on the Hugging Face hub here, named
# ``ggml-<model>.bin`` (e.g. ggml-large-v3.bin). Downloaded lazily and cached
# under HF_HOME (the worker mounts that on the model-cache volume).
_GGML_HF_REPO = "ggerganov/whisper.cpp"

# Silero VAD models in GGML format live in a separate ggml-org repo; resolved
# and cached exactly like the main model above.
_VAD_HF_REPO = "ggml-org/whisper-vad"

# Local progress band for the CLI run. We cannot stream whisper.cpp progress
# without parsing stderr, so we report load -> running -> done coarsely; the
# 0.0-0.1 slice covers model resolution to match the whisperx stage's feel.
_RUN_PROGRESS_START = 0.1


class WhisperCppTranscribeStage:
    """Transcribe via the whisper.cpp HIP CLI — the AMD/ROCm transcription path.

    CTranslate2 (which faster-whisper / whisperx use) has no ROCm backend, so
    the whisperx transcription path cannot drive an AMD GPU. whisper.cpp's HIP
    (ggml) backend runs natively on gfx1151. This stage shells out to the
    ``whisper-cli`` binary, parses its ``-oj`` JSON, and emits the *same*
    context keys the whisperx :class:`~whisper_ui.pipeline.transcribe.TranscribeStage`
    does (``transcription_result`` + ``whisperx_audio``) so the downstream
    AlignStage and DiarizeStage run unchanged.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_WHISPER_MODEL,
        *,
        binary: str = "whisper-cli",
        model_dir: str | Path | None = None,
        threads: int = 0,
        device: str = "rocm",
        vad: bool = True,
        vad_model: str = "ggml-silero-v5.1.2.bin",
        max_context: int = 0,
    ) -> None:
        # ``device`` is accepted for interface symmetry with TranscribeStage;
        # whisper.cpp selects the GPU itself (HIP_VISIBLE_DEVICES env), so it is
        # not passed to the CLI.
        self._model_name = model_name
        self._binary = binary
        self._model_dir = Path(model_dir) if model_dir else None
        self._threads = threads
        self._device = device
        self._vad = vad
        self._vad_model = vad_model
        self._max_context = max_context

    @property
    def name(self) -> str:
        return "transcribe"

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]:
        audio_path = context["audio_path"]
        language = context.get("language", "zh")

        if on_progress:
            on_progress(0.0, TRANSCRIBE_LOADING)

        try:
            model_path = self._resolve_model_path()
            vad_model_path = self._resolve_vad_model_path() if self._vad else None

            if on_progress:
                on_progress(_RUN_PROGRESS_START, TRANSCRIBE_RUNNING)

            data = self._run_whisper_cli(model_path, audio_path, language, vad_model_path)
            transcription = self._to_whisperx_result(data, language)

            # AlignStage consumes ``whisperx_audio`` (the decoded 16 kHz array);
            # load it the same way the whisperx path does so the contract is
            # byte-for-byte identical regardless of transcription backend. The
            # import is localized so a genuinely missing whisperx is reported
            # accurately, while unrelated ImportErrors (e.g. huggingface_hub
            # raised during model resolution above) are not misattributed to it.
            try:
                import whisperx
            except ImportError as err:
                raise TranscriptionError("whisperx is not installed (needed to load audio for alignment).") from err

            context["transcription_result"] = transcription
            context["whisperx_audio"] = whisperx.load_audio(audio_path)

            if on_progress:
                on_progress(1.0, TRANSCRIBE_DONE)
            return context

        except BaseTimeoutException:
            # Let RQ's death penalty propagate so the task is classified as a
            # timeout, not a stage-level transcription failure.
            raise
        except TranscriptionError:
            raise
        except FileNotFoundError as err:
            raise TranscriptionError(
                f"whisper.cpp binary '{self._binary}' not found. It must be built into the rocm worker image."
            ) from err
        except Exception as e:
            raise TranscriptionError(f"Transcription failed: {e}") from e

    def cleanup(self) -> None:
        # The CLI subprocess has already exited and released the GPU; there is
        # no in-process model handle to free.
        return None

    def _resolve_model_path(self) -> str:
        """Return a path to the GGML model file, downloading it if needed.

        A pre-baked file under ``model_dir`` wins (offline images); otherwise
        fetch ``ggml-<model>.bin`` from the Hugging Face hub, cached under
        HF_HOME so repeat runs do not re-download.
        """
        filename = f"ggml-{self._model_name}.bin"
        if self._model_dir is not None:
            candidate = self._model_dir / filename
            if candidate.is_file():
                return str(candidate)
        from huggingface_hub import hf_hub_download

        return hf_hub_download(repo_id=_GGML_HF_REPO, filename=filename)

    def _resolve_vad_model_path(self) -> str:
        """Return a path to the GGML VAD model file, downloading it if needed.

        Resolution mirrors :meth:`_resolve_model_path`. A failure here fails
        the stage rather than silently falling back to a no-VAD run: a run
        without VAD can complete "successfully" with a hallucinated transcript,
        which is exactly the failure mode VAD exists to prevent. Offline
        deployments can pre-bake the file under ``model_dir``.
        """
        if self._model_dir is not None:
            candidate = self._model_dir / self._vad_model
            if candidate.is_file():
                return str(candidate)
        from huggingface_hub import hf_hub_download

        return hf_hub_download(repo_id=_VAD_HF_REPO, filename=self._vad_model)

    def _run_whisper_cli(
        self, model_path: str, audio_path: str, language: str, vad_model_path: str | None = None
    ) -> dict[str, Any]:
        """Run whisper-cli with JSON output and return the parsed document."""
        with tempfile.TemporaryDirectory() as tmp:
            out_prefix = str(Path(tmp) / "out")
            cmd = [
                self._binary,
                "-m",
                model_path,
                "-f",
                str(audio_path),
                "-l",
                language,
                "-oj",
                "-of",
                out_prefix,
            ]
            if self._threads and self._threads > 0:
                cmd += ["-t", str(self._threads)]
            if vad_model_path is not None:
                cmd += ["--vad", "-vm", vad_model_path]
            if self._max_context >= 0:
                cmd += ["-mc", str(self._max_context)]

            logger.info(
                "whisper-cli starting (vad=%s max_context=%d language=%s)",
                vad_model_path is not None,
                self._max_context,
                language,
                extra={
                    "event": "whispercpp_cli",
                    "vad": vad_model_path is not None,
                    "max_context": self._max_context,
                    "language": language,
                },
            )

            # encoding/errors (not text=True) so non-ASCII output — e.g. zh
            # transcripts or file names — cannot raise UnicodeDecodeError.
            proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                # Some builds emit diagnostics on stdout; fall back to it.
                detail = proc.stderr.strip() or proc.stdout.strip()
                raise TranscriptionError(f"whisper-cli failed (exit {proc.returncode}): {detail[:500]}")

            json_path = Path(f"{out_prefix}.json")
            if not json_path.is_file():
                raise TranscriptionError("whisper-cli produced no JSON output")
            return json.loads(json_path.read_text(encoding="utf-8"))

    @staticmethod
    def _to_whisperx_result(data: dict[str, Any], requested_language: str) -> dict[str, Any]:
        """Adapt whisper.cpp ``-oj`` JSON to the whisperx transcription contract.

        whisper.cpp emits ``result.language`` and a ``transcription`` array of
        segments with millisecond ``offsets`` and ``text``; AlignStage expects
        ``{"language", "segments": [{"start", "end", "text"}]}`` with seconds.

        When the JSON lacks ``result.language``, fall back to the language the
        job requested — it is what ``-l`` decoded with, and a truthy
        ``"unknown"`` here would silently disable the zh-only postprocess and
        LLM gates downstream. Only an ``auto`` request is genuinely unknown.
        """
        fallback = "unknown" if requested_language == AUTO_LANGUAGE else requested_language
        if not isinstance(data, dict):
            return {"language": fallback, "segments": []}
        result = data.get("result")
        language = (result.get("language") if isinstance(result, dict) else None) or fallback
        segments: list[dict[str, Any]] = []
        for seg in data.get("transcription") or []:
            if not isinstance(seg, dict):
                continue
            offsets = seg.get("offsets") if isinstance(seg.get("offsets"), dict) else {}
            # ``or 0`` guards an explicit ``null`` offset (None / 1000.0 -> TypeError).
            segments.append(
                {
                    "start": (offsets.get("from") or 0) / 1000.0,
                    "end": (offsets.get("to") or 0) / 1000.0,
                    "text": (seg.get("text") or "").strip(),
                }
            )
        return {"language": language, "segments": segments}
