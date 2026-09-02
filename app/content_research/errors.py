"""Stable public error types for Content Research application boundaries."""

from __future__ import annotations


class ContentResearchError(ValueError):
    """Base error for Content Research service failures."""


class ContentResearchNotFoundError(ContentResearchError):
    """Raised when a requested Content Research object is missing."""


class ContentResearchRunNotFoundError(ContentResearchNotFoundError):
    """Raised when the exact Run is absent from the active data generation."""


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


class ContentResearchSnapshotBehindError(RuntimeError):
    """A bounded causal read ended below the requested revision."""

    def __init__(self, observed_revision: int, minimum_revision: int) -> None:
        super().__init__("snapshot minimum revision was not reached")
        self.observed_revision = observed_revision
        self.minimum_revision = minimum_revision


class ContentResearchSnapshotUnavailableError(RuntimeError):
    """No trustworthy Domain Trace snapshot could be composed."""

    def __init__(self, code: str) -> None:
        super().__init__("domain trace snapshot is unavailable")
        self.code = code


class ReportPublicationMaterializationError(RuntimeError):
    """Carry the exact persisted publication across the materialization boundary."""

    def __init__(self, publication_id: str, cause: Exception) -> None:
        super().__init__(str(cause) or "Report publication failed.")
        self.publication_id = publication_id
