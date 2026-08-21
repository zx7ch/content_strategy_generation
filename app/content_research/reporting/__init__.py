"""Immutable report-domain contracts for the governed research snapshot."""

from app.content_research.reporting.composer import ResearchReportComposer
from app.content_research.reporting.contracts import (
    CitationAnchor,
    ReportDraft,
    ReportFaithfulnessDecision,
    ReportPublication,
    ReportSection,
)
from app.content_research.reporting.execution import ReportExecutionService
from app.content_research.reporting.faithfulness import (
    FaithfulnessEvaluation,
    ReportFaithfulnessEvaluator,
    ReportSemanticAuditor,
    SemanticAuditResult,
    UnavailableReportSemanticAuditor,
)
from app.content_research.reporting.publication_materializer import ReportPublicationMaterializer
from app.content_research.reporting.read_model import ExecutionTraceReader

__all__ = [
    "CitationAnchor",
    "ReportDraft",
    "ReportFaithfulnessDecision",
    "ReportPublication",
    "ReportSection",
    "ResearchReportComposer",
    "ReportExecutionService",
    "ReportPublicationMaterializer",
    "ExecutionTraceReader",
    "FaithfulnessEvaluation",
    "ReportFaithfulnessEvaluator",
    "ReportSemanticAuditor",
    "SemanticAuditResult",
    "UnavailableReportSemanticAuditor",
]
