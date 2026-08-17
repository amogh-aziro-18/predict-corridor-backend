"""Application error taxonomy (Sprint 1 — base types only)."""


class AppError(Exception):
    """Base application error."""

    def __init__(self, message: str, *, code: str = "app_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class NotFoundError(AppError):
    def __init__(self, message: str = "Resource not found") -> None:
        super().__init__(message, code="not_found")


class ValidationAppError(AppError):
    def __init__(self, message: str = "Validation failed") -> None:
        super().__init__(message, code="validation_error")


class PluginError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="plugin_error")
