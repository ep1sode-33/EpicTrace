class EpicTraceError(Exception):
    """Base for domain errors."""


class ProjectNotFound(EpicTraceError):
    pass


class SourceFileNotFound(EpicTraceError):
    pass


class InvalidSourcePath(EpicTraceError):
    """Source exists but is not a regular file (directory, device, etc.)."""


class SourceUnreadable(EpicTraceError):
    """Source cannot be read (permission)."""
