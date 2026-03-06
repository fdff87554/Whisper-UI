class WhisperUIError(Exception):
    pass


class PipelineError(WhisperUIError):
    pass


class PreprocessError(PipelineError):
    pass


class TranscriptionError(PipelineError):
    pass


class AlignmentError(PipelineError):
    pass


class DiarizationError(PipelineError):
    pass


class StorageError(WhisperUIError):
    pass


class ExportError(WhisperUIError):
    pass
