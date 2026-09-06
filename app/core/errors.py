"""Error taxonomy for typed, non-leaking API errors.

Routes that need a specific status code raise one of these instead of
`HTTPException(500, detail=str(exc))` (which leaks internal exception text --
stack traces, SQL fragments, file paths -- straight to the client). Global
handlers registered in app/main.py convert these to safe JSON responses, and
convert any OTHER unhandled exception to a generic 500 with no internal
detail, logging the real traceback server-side only.
"""


class AppError(Exception):
    """Base class for errors with a known, safe-to-show status and message."""

    status_code: int = 500

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ValidationError(AppError):
    status_code = 400


class PermissionDeniedError(AppError):
    status_code = 403


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409


class UpstreamUnavailableError(AppError):
    status_code = 503
