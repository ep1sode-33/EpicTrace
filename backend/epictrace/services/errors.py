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


class ActiveSessionExists(Exception):
    """已有一个 recording 中的 session,不允许再开(单一活动 session)。"""


class CaptureSessionNotFound(Exception):
    def __init__(self, session_id: int) -> None:
        super().__init__(f"capture session not found: {session_id}")
        self.session_id = session_id


class SessionNotRecording(Exception):
    """对一个非 recording 的 session 追加事件/暂停/继续。"""


class SessionAlreadyOrganized(Exception):
    """对已 organized 的 session 再次 organize。"""


class SessionNotStaged(Exception):
    """对一个非 staged 的 session(如仍在 recording)做 organize。
    录制中不应中途归类;须先 stop(→staged)再 organize。"""
