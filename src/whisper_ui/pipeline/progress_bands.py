"""Stage progress weight bands used by the pipeline orchestrator and worker tasks.

Each mapping declares the global-progress band `(start, end)` assigned to a
stage name. Stage implementations report local progress in [0, 1] which is
linearly mapped into its band, so the user-facing progress bar advances
smoothly across stages without jumps.

Four distinct layouts are kept instead of computing them dynamically because
production jobs enqueued under an older layout must continue to report
progress with the bands they started with — changing a shared dict would make
their progress bar jump when the worker is redeployed.
"""

from __future__ import annotations

StageWeights = dict[str, tuple[float, float]]


STAGE_WEIGHTS: StageWeights = {
    "preprocess": (0.00, 0.05),
    "transcribe": (0.05, 0.55),
    "align": (0.55, 0.65),
    "diarize": (0.65, 0.90),
    "assign_speakers": (0.90, 0.95),
    "postprocess": (0.95, 1.00),
}


STAGE_WEIGHTS_WITH_DOWNLOAD: StageWeights = {
    "download": (0.00, 0.15),
    "preprocess": (0.15, 0.20),
    "transcribe": (0.20, 0.60),
    "align": (0.60, 0.70),
    "diarize": (0.70, 0.90),
    "assign_speakers": (0.90, 0.95),
    "postprocess": (0.95, 1.00),
}


STAGE_WEIGHTS_WITH_LLM: StageWeights = {
    "preprocess": (0.00, 0.05),
    "transcribe": (0.05, 0.50),
    "align": (0.50, 0.60),
    "diarize": (0.60, 0.85),
    "assign_speakers": (0.85, 0.90),
    "postprocess": (0.90, 0.92),
    "llm_correction": (0.92, 1.00),
}


STAGE_WEIGHTS_WITH_DOWNLOAD_AND_LLM: StageWeights = {
    "download": (0.00, 0.12),
    "preprocess": (0.12, 0.17),
    "transcribe": (0.17, 0.55),
    "align": (0.55, 0.65),
    "diarize": (0.65, 0.85),
    "assign_speakers": (0.85, 0.90),
    "postprocess": (0.90, 0.92),
    "llm_correction": (0.92, 1.00),
}


__all__ = [
    "STAGE_WEIGHTS",
    "STAGE_WEIGHTS_WITH_DOWNLOAD",
    "STAGE_WEIGHTS_WITH_DOWNLOAD_AND_LLM",
    "STAGE_WEIGHTS_WITH_LLM",
    "StageWeights",
]
