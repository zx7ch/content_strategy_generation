import logging

from app.core.logging import configure_logging


def test_runtime_logging_writes_runtime_events_to_a_file(tmp_path):
    log_file = tmp_path / "runtime.log"

    configure_logging(force=True, log_file=str(log_file))
    logging.getLogger("runtime-test").error("runtime process stopped unexpectedly")

    assert "runtime process stopped unexpectedly" in log_file.read_text(encoding="utf-8")
