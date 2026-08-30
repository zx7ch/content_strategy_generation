"""Stable public error types for Content Research application boundaries."""

from __future__ import annotations


class ContentResearchError(ValueError):
    """Base error for Content Research service failures."""


class ContentResearchNotFoundError(ContentResearchError):
    """Raised when a requested Content Research object is missing."""


class ContentResearchValidationError(ContentResearchError):
    """Raised when a request payload is invalid."""


class ContentResearchStateConflictError(ContentResearchValidationError):
    """Raised when a valid action is unsafe for the current durable state."""

    def __init__(self, message: str, *, error_code: str, suggested_action: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.suggested_action = suggested_action


class ContentResearchReportIntegrityError(RuntimeError):
    """Raised when an existing published report cannot be safely projected."""


class ReportPublicationMaterializationError(RuntimeError):
    """Carry the exact persisted publication across the materialization boundary."""

    def __init__(self, publication_id: str, cause: Exception) -> None:
        super().__init__(str(cause) or "Report publication failed.")
        self.publication_id = publication_id
