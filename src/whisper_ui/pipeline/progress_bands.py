"""Stage progress weight bands used by the pipeline orchestrator and worker tasks.

Each band is a ``(start, end)`` tuple that maps a stage's local
``[0, 1]`` progress into the global progress bar. The bands are
generated from a single relative-weight table so adding or
re-ordering an optional stage stays a one-line change: tweak the
relative weight and ``build_stage_weights`` re-derives every band.

The exact percentages drift a couple of points from the legacy
hand-tuned tables (because the same relative weights now get
normalised against the active stage set), but the bands are an
operator-facing hint not a benchmark, so the drift is acceptable.
"""

from __future__ import annotations

StageWeights = dict[str, tuple[float, float]]


# Relative weights — higher means the stage occupies a longer slice of the
# global progress bar. The numbers are picked from the legacy hand-tuned
# tables and kept here in one place so a maintainer only needs to touch
# one literal when re-balancing.
_STAGE_RELATIVE_WEIGHTS = {
    "download": 15,
    "preprocess": 5,
    "transcribe": 50,
    "align": 10,
    "diarize": 25,
    "assign_speakers": 5,
    "postprocess": 5,
    "llm_correction": 8,
}
# When the LLM correction stage is appended after postprocess, postprocess
# shrinks so the visible bar still moves during the LLM call. Matches the
# legacy STAGE_WEIGHTS_WITH_LLM layout where postprocess was 0.90 → 0.92.
_POSTPROCESS_WEIGHT_WITH_LLM = 2


def build_stage_weights(*, has_download: bool, has_llm: bool) -> StageWeights:
    """Derive the per-stage ``(start, end)`` bands for a pipeline shape.

    ``has_download`` prepends the download stage; ``has_llm`` shrinks
    postprocess and appends the llm_correction stage. The returned dict
    is keyed by stage name (matching ``PipelineStage.name``) so the
    orchestrator can look up each band by name without caring about
    pipeline shape.
    """
    stages: list[tuple[str, int]] = []
    if has_download:
        stages.append(("download", _STAGE_RELATIVE_WEIGHTS["download"]))
    stages.extend(
        [
            ("preprocess", _STAGE_RELATIVE_WEIGHTS["preprocess"]),
            ("transcribe", _STAGE_RELATIVE_WEIGHTS["transcribe"]),
            ("align", _STAGE_RELATIVE_WEIGHTS["align"]),
            ("diarize", _STAGE_RELATIVE_WEIGHTS["diarize"]),
            ("assign_speakers", _STAGE_RELATIVE_WEIGHTS["assign_speakers"]),
        ]
    )
    postprocess_w = _POSTPROCESS_WEIGHT_WITH_LLM if has_llm else _STAGE_RELATIVE_WEIGHTS["postprocess"]
    stages.append(("postprocess", postprocess_w))
    if has_llm:
        stages.append(("llm_correction", _STAGE_RELATIVE_WEIGHTS["llm_correction"]))

    total = sum(weight for _, weight in stages)
    bands: StageWeights = {}
    cursor = 0
    for name, weight in stages:
        start = cursor / total
        end = (cursor + weight) / total
        bands[name] = (round(start, 4), round(end, 4))
        cursor += weight
    return bands


# Default band layout (no optional stages). Exposed so callers that have
# no pipeline shape on hand — chiefly the legacy single-process
# orchestrator default — still get a sensible mapping.
DEFAULT_STAGE_WEIGHTS: StageWeights = build_stage_weights(has_download=False, has_llm=False)


__all__ = [
    "DEFAULT_STAGE_WEIGHTS",
    "StageWeights",
    "build_stage_weights",
]
