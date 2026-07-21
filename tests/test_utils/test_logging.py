import json
import logging

from utils.logging import JSONFormatter, configure_logging, get_logger


def test_json_formatter_produces_valid_json():
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    formatted = json.loads(JSONFormatter().format(record))
    assert formatted["message"] == "hello world"
    assert formatted["level"] == "INFO"
    assert formatted["logger"] == "test.logger"
    assert "timestamp" in formatted


def test_configure_logging_attaches_json_handler():
    configure_logging(level="DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JSONFormatter)


def test_get_logger_returns_named_logger():
    logger = get_logger("trading_bot.scanner")
    assert logger.name == "trading_bot.scanner"
