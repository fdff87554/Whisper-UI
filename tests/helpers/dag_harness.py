"""Shared stage-stub harness for DAG dispatcher tests.

Used by the unit DAG parallelism tests and the queue-split throughput
tests: recorder stages replace every heavy PipelineStage so a real
SimpleWorker can drain a full pipeline against fakeredis in
milliseconds, while the timeline records how stages interleaved.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any
from unittest.mock import MagicMock

from whisper_ui.core.models import TranscriptResult
from whisper_ui.worker.runtime import WorkerRuntime


class RecorderStage:
    """Drop-in PipelineStage double that records its name + the order it ran.

    ``timeline`` is the list the test reads after the burst to check how
    stages interleaved across jobs. Each recorder returns ``new_keys`` as
    context updates so the dispatcher's declared output_keys behaviour is
    exercised unchanged.
    """

    def __init__(self, name: str, timeline: list, new_keys: dict[str, Any], delay: float = 0.0):
        self._name = name
        self._timeline = timeline
        self._new_keys = new_keys
        self._delay = delay

    @property
    def name(self) -> str:
        return self._name

    def execute(self, context: dict, on_progress=None) -> dict:
        self._timeline.append((self._name, time.monotonic()))
        if self._delay:
            time.sleep(self._delay)
        if on_progress:
            on_progress(1.0, f"{self._name}-done")
        out = dict(context)
        out.update(self._new_keys)
        return out

    def cleanup(self) -> None:
        pass


@contextlib.contextmanager
def stub_stages(monkeypatch, timeline: list):
    """Replace every heavy PipelineStage constructor with recorder doubles.

    ``stage_tasks`` imports stage classes at module load time so the patches
    must target the attribute path the lambdas inside run_* functions use.
    """
    stubs = {
        "PreprocessStage": RecorderStage("preprocess", timeline, {"audio_path": "/tmp/fake.wav", "duration": 10.0}),
        "TranscribeStage": RecorderStage("transcribe", timeline, {"transcription_result": {"segments": []}}),
        "AlignStage": RecorderStage("align", timeline, {"aligned_result": {"segments": []}}),
        "DiarizeStage": RecorderStage("diarize", timeline, {"diarize_result": [("SPK0", 0.0, 1.0)]}),
        "AssignSpeakersStage": RecorderStage("assign_speakers", timeline, {"final_result": {"segments": []}}),
        "PostprocessStage": RecorderStage(
            "postprocess",
            timeline,
            {"transcript_result": TranscriptResult(language="zh", duration=10.0)},
        ),
    }

    for attr, stage in stubs.items():
        monkeypatch.setattr(
            f"whisper_ui.worker.stage_tasks.{attr}",
            lambda *args, _s=stage, **kwargs: _s,
        )
    yield


@contextlib.contextmanager
def fake_runtime_factory(fake_redis, fake_settings, db, filestore):
    """Install a build_worker_runtime stand-in that returns the same fake
    resources for every stage task invocation in the test.
    """
    runtime = WorkerRuntime(
        settings=fake_settings,
        redis=fake_redis,
        reporter=MagicMock(),
        db=db,
        filestore=filestore,
    )

    @contextlib.contextmanager
    def _builder(job_id, *, generation=None):
        yield runtime

    yield runtime, _builder


def install_runtime(monkeypatch, builder):
    monkeypatch.setattr("whisper_ui.worker.stage_tasks.build_worker_runtime", builder)
    monkeypatch.setattr("whisper_ui.worker.pipeline_dispatcher.build_worker_runtime", builder)
