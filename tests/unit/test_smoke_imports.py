"""
Smoke tests for key package and module imports.

These tests verify that all critical modules can be imported
without errors, ensuring the basic project structure is intact.

Import errors to catch:
- Wrong import paths (e.g., 'from config' vs 'from app.config')
- Missing dependencies
- Circular imports
- Syntax errors in module initialization
"""

import pytest


class TestPackageImports:
    """Test that key packages can be imported."""

    def test_import_app(self):
        """Test that app package can be imported."""
        import app
        assert hasattr(app, '__version__') or True  # Just verify import works

    def test_import_app_config(self):
        """Test that app.config module can be imported."""
        from app import config
        assert config is not None
        assert hasattr(config, 'settings')

    def test_import_app_models(self):
        """Test that app.models module can be imported."""
        from app import models
        assert models is not None

    def test_import_app_services(self):
        """Test that app.services module can be imported."""
        from app import services
        assert services is not None

    def test_import_app_agents(self):
        """Test that app.agents module can be imported."""
        from app import agents
        assert agents is not None

    def test_import_app_api(self):
        """Test that app.api module can be imported."""
        from app import api
        assert api is not None

    def test_import_app_memory(self):
        """Test that app.memory module can be imported."""
        from app import memory
        assert memory is not None

    def test_import_app_llm(self):
        """Test that app.llm module can be imported."""
        from app import llm
        assert llm is not None

    def test_import_app_logging_config(self):
        """Test that app.logging_config module can be imported."""
        import app.logging_config as logging_config
        assert logging_config is not None


class TestServiceImports:
    """Test specific service modules can be imported."""

    def test_import_xhs_spider(self):
        """Test xhs_spider service imports correctly."""
        from app.services import xhs_spider
        assert hasattr(xhs_spider, 'XHSSpiderClient')
        assert hasattr(xhs_spider, 'XHSPost')
        assert hasattr(xhs_spider, 'SpiderTransientError')
        assert hasattr(xhs_spider, 'SpiderPermanentError')

    def test_import_rag_service(self):
        """Test rag_service imports correctly."""
        from app.services import rag_service
        assert hasattr(rag_service, 'RAGService')
        assert hasattr(rag_service, 'QualityScore')

    def test_import_engagement_analyzer(self):
        """Test engagement_analyzer imports correctly."""
        from app.services import engagement_analyzer
        assert hasattr(engagement_analyzer, 'EngagementAnalyzer')


class TestAgentImports:
    """Test specific agent modules can be imported."""

    def test_import_content_strategy_agent(self):
        """Test content_strategy_agent imports correctly."""
        from app.agents import content_strategy_agent
        assert hasattr(content_strategy_agent, 'ContentStrategyAgent')

    def test_import_content_generation_agent(self):
        """Test content_generation_agent imports correctly."""
        from app.agents import content_generation_agent
        assert hasattr(content_generation_agent, 'ContentGenerationAgent')


class TestMemoryImports:
    """Test memory modules can be imported."""

    def test_import_session_state(self):
        """Test session_state imports correctly."""
        from app.memory import session_state
        assert hasattr(session_state, 'SessionManager')

    def test_import_job_store(self):
        """Test job_store imports correctly."""
        from app.memory import job_store
        assert hasattr(job_store, 'JobStore')
        assert hasattr(job_store, 'JobRecord')


class TestWorkerImports:
    """Test worker modules can be imported."""

    def test_import_job_worker(self):
        """Test job_worker imports correctly."""
        from app.workers import job_worker
        assert hasattr(job_worker, 'JobWorker')


class TestLLMImports:
    """Test LLM modules can be imported."""

    def test_import_llm_client(self):
        """Test llm_client imports correctly."""
        from app.llm import client
        assert hasattr(client, 'LLMClient')
        assert hasattr(client, 'LLMProvider')


class TestModelImports:
    """Test data models can be imported."""

    def test_import_session_models(self):
        """Test session models import correctly."""
        from app.models.session import (
            Session, SessionStage, SessionError,
            ContentStrategy, PlatformPreference, RetryStats
        )
        assert Session is not None
        assert SessionStage is not None

    def test_import_schema_models(self):
        """Test schema models import correctly."""
        from app.models.schemas import (
            Result, XHSNote, WebSearchResult,
            ContentStrategyRequest, ContentStrategyResponse
        )
        assert Result is not None
        assert XHSNote is not None
        assert ContentStrategyRequest is not None
