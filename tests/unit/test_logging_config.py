"""Tests for app.logging_config contract facade."""

from __future__ import annotations

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

import app.logging_config as logging_config
from app.core.context import clear_correlation_id, set_correlation_id


@pytest.fixture(autouse=True)
def _reset_context() -> None:
    clear_correlation_id()


def test_setup_logging_uses_human_readable_in_development() -> None:
    with patch.object(logging_config.settings, "APP_ENV", "development"):
        with patch("app.logging_config._configure_logging") as mock_config:
            logging_config.setup_logging(force=True)
            mock_config.assert_called_once()
            assert mock_config.call_args.kwargs["json_format"] is False


def test_setup_logging_uses_json_in_production() -> None:
    with patch.object(logging_config.settings, "APP_ENV", "production"):
        with patch("app.logging_config._configure_logging") as mock_config:
            logging_config.setup_logging(force=True)
            mock_config.assert_called_once()
            assert mock_config.call_args.kwargs["json_format"] is True


def test_get_logger_binds_contract_defaults() -> None:
    with patch("app.logging_config._get_logger") as mock_get:
        bound = MagicMock()
        raw = MagicMock()
        raw.bind.return_value = bound
        mock_get.return_value = raw

        logger = logging_config.get_logger("test.logger", component="worker")
        assert logger is bound

        bind_kwargs = raw.bind.call_args.kwargs
        assert bind_kwargs["event_name"] == "app_log"
        assert bind_kwargs["component"] == "worker"
        assert bind_kwargs["session_id"] is None
        assert bind_kwargs["job_id"] is None
        assert bind_kwargs["stage"] is None


def test_log_event_applies_required_fields_and_trace_id_from_context() -> None:
    set_correlation_id("trc_123")
    fake_logger = MagicMock()

    logging_config.log_event(
        fake_logger,
        event_name="job_enqueued",
        level="info",
        component="worker",
        session_id="sess_1",
        job_id="job_1",
        stage="strategy",
        job_type="strategy",
    )

    fake_logger.info.assert_called_once()
    _, kwargs = fake_logger.info.call_args
    assert kwargs["event_name"] == "job_enqueued"
    assert kwargs["trace_id"] == "trc_123"
    assert kwargs["session_id"] == "sess_1"
    assert kwargs["job_id"] == "job_1"
    assert kwargs["stage"] == "strategy"
    assert kwargs["component"] == "worker"
    assert kwargs["job_type"] == "strategy"


def test_log_event_rejects_invalid_level() -> None:
    with pytest.raises(ValueError):
        logging_config.log_event(MagicMock(), event_name="x", level="fatal")


def test_required_event_registry_contains_budget_events() -> None:
    assert logging_config.is_required_event("budget_degrade_applied") is True
    assert logging_config.is_required_event("budget_exceeded") is True


def test_required_event_registry_covers_p1_5_mandatory_events() -> None:
    required = {
        "job_enqueued",
        "job_leased",
        "job_retry_scheduled",
        "job_completed",
        "job_failed",
        "llm_call_completed",
        "budget_degrade_applied",
        "budget_exceeded",
    }
    assert required.issubset(logging_config.REQUIRED_EVENTS)


def test_missing_contract_keys_checker() -> None:
    payload = {"event_name": "x", "trace_id": "t", "session_id": None}
    missing = logging_config.missing_contract_keys(payload)
    assert "job_id" in missing
    assert "stage" in missing
    assert "component" in missing
