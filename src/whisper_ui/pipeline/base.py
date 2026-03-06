from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class ProgressCallback(Protocol):
    def __call__(self, progress: float, message: str) -> None: ...


@runtime_checkable
class PipelineStage(Protocol):
    @property
    def name(self) -> str: ...

    def execute(self, context: dict[str, Any], on_progress: ProgressCallback | None = None) -> dict[str, Any]: ...

    def cleanup(self) -> None: ...
