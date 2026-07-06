import json
import logging
import pytest
from unittest.mock import patch
from shared.logger import get_logger, JSONFormatter


def test_get_logger_returns_logger_with_service_name():
    logger = get_logger("test-service")
    assert logger.name == "test-service"


def test_get_logger_has_handler():
    logger = get_logger("test-service-2")
    assert len(logger.handlers) > 0


def test_json_formatter_outputs_valid_json():
    formatter = JSONFormatter("test-service")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["message"] == "hello world"
    assert parsed["level"] == "INFO"
    assert parsed["service"] == "test-service"
    assert "timestamp" in parsed


def test_json_formatter_includes_exception_info():
    formatter = JSONFormatter("test-service")
    try:
        raise ValueError("test error")
    except ValueError:
        import sys
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="something failed",
        args=(),
        exc_info=exc_info,
    )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert "exception" in parsed
    assert "ValueError" in parsed["exception"]


def test_get_logger_respects_log_level_from_config():
    import logging
    with patch("shared.logger.load_config", return_value={"log_level": "DEBUG"}):
        logger = get_logger("config-test-service")
    assert logger.level == logging.DEBUG
