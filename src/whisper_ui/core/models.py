from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None


@dataclass
class TranscriptResult:
    segments: list[Segment] = field(default_factory=list)
    language: str = "zh"
    duration: float = 0.0


@dataclass
class Job:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    filename: str = ""
    filepath: str = ""
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    progress_message: str = ""
    language: str = "zh"
    num_speakers: int | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    error: str | None = None
    result_path: str | None = None
    duration: float | None = None

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC).isoformat()
