import json
import logging
import stat
from dataclasses import dataclass

from parishkit.config import ConfigError
from parishkit.logging import (
    DEFAULT_BACKUP_COUNT,
    DEFAULT_MAX_BYTES,
    CompressingRotatingFileHandler,
    JsonLogFormatter,
    describe_handlers,
    log_extra,
    parse_log_level,
    setup_logging,
)


def test_setup_logging_console_defaults():
    """With no options, logging is one console StreamHandler at WARNING level."""
    logger = setup_logging(logger_name="test.console.default")

    handlers = describe_handlers(logger)

    assert handlers == [{"type": "StreamHandler", "level": logging.WARNING}]


def test_setup_logging_debug_file_handler(tmp_path):
    """Debug mode with rotation attaches a compressing file handler that writes."""
    log_file = tmp_path / "parishkit.log"
    logger = setup_logging(
        debug=True,
        log_file=log_file,
        logger_name="test.file.debug",
        rotate=True,
        max_bytes=1000,
    )

    logger.debug("hello")

    assert log_file.exists()
    assert stat.S_IMODE(log_file.stat().st_mode) == 0o600
    payload = json.loads(log_file.read_text(encoding="utf-8"))
    assert payload["level"] == "DEBUG"
    assert payload["logger"] == "test.file.debug"
    assert payload["message"] == "hello"
    assert payload["timestamp"]
    assert any(
        isinstance(handler, CompressingRotatingFileHandler)
        for handler in logger.handlers
    )


def test_default_file_rotation_keeps_large_compressed_history(tmp_path):
    """Default file logging rotates at 50 MB and keeps 50 compressed backups."""
    logger = setup_logging(
        log_file=tmp_path / "parishkit.log",
        logger_name="test.file.rotation.defaults",
    )

    file_handler = next(
        handler
        for handler in logger.handlers
        if isinstance(handler, CompressingRotatingFileHandler)
    )

    assert file_handler.maxBytes == DEFAULT_MAX_BYTES == 50_000_000
    assert file_handler.backupCount == DEFAULT_BACKUP_COUNT == 50


def test_file_and_console_handlers_use_different_formatters(tmp_path):
    """File handlers emit JSONL while console output keeps the text formatter."""
    logger = setup_logging(
        verbose=True,
        log_file=tmp_path / "parishkit.log",
        logger_name="test.file.formatters",
        rotate=False,
    )

    handlers = {handler.__class__.__name__: handler for handler in logger.handlers}

    assert isinstance(handlers["FileHandler"].formatter, JsonLogFormatter)
    assert not isinstance(handlers["StreamHandler"].formatter, JsonLogFormatter)


def test_json_log_formatter_includes_structured_object(tmp_path):
    """Structured context is JSON in files without changing message text."""

    @dataclass(frozen=True)
    class Item:
        """Tiny dataclass used to prove structured objects are converted."""

        name: str
        count: int

    log_file = tmp_path / "parishkit.log"
    logger = setup_logging(
        verbose=True,
        log_file=log_file,
        logger_name="test.file.object",
        rotate=False,
    )

    logger.info(
        "Processed %s item(s): %s",
        1,
        "sample",
        extra=log_extra([Item("sample", 3)]),
    )

    raw_log = log_file.read_text(encoding="utf-8")
    assert raw_log.count("\n") == 1
    assert raw_log.index('"message"') < raw_log.index('"extra"')
    payload = json.loads(raw_log)
    assert payload["message"] == "Processed 1 item(s): sample"
    assert payload["extra"] == [{"count": 3, "name": "sample"}]


def test_parishkit_tool_logging_captures_package_and_sibling_records(tmp_path):
    """Tool logging also captures shared ParishKit helper records."""
    log_file = tmp_path / "parishkit.log"
    logger = setup_logging(
        verbose=True,
        log_file=log_file,
        logger_name="parishkit.tool",
        rotate=False,
    )

    logger.info("tool record")
    logging.getLogger("parishkit.shared").warning("shared record")
    logging.getLogger("parishkit").error("package record")

    payloads = [
        json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()
    ]
    assert [payload["message"] for payload in payloads] == [
        "tool record",
        "shared record",
        "package record",
    ]
    assert logger.handlers == []


def test_parishkit_logging_reconfiguration_clears_stale_child_handlers(tmp_path):
    """Reconfiguring a ParishKit tool moves sibling logs to the active file."""
    first_file = tmp_path / "first.log"
    second_file = tmp_path / "second.log"
    first_logger = setup_logging(
        verbose=True,
        log_file=first_file,
        logger_name="parishkit.first",
        rotate=False,
    )
    first_logger.warning("first before")

    setup_logging(
        verbose=True,
        log_file=second_file,
        logger_name="parishkit.second",
        rotate=False,
    )
    first_logger.warning("first after")

    first_payloads = [
        json.loads(line) for line in first_file.read_text(encoding="utf-8").splitlines()
    ]
    second_payloads = [
        json.loads(line)
        for line in second_file.read_text(encoding="utf-8").splitlines()
    ]
    assert [payload["message"] for payload in first_payloads] == ["first before"]
    assert [payload["message"] for payload in second_payloads] == ["first after"]


def test_compressed_rotation_retains_multiple_backups(tmp_path):
    """Enough log volume rolls over into several gzipped backup files."""
    log_file = tmp_path / "parishkit.log"
    logger = setup_logging(
        logger_name="test.rotation.retention",
        log_file=log_file,
        rotate=True,
        max_bytes=80,
        backup_count=3,
    )

    # Each message comfortably exceeds max_bytes, forcing repeated rotations.
    for index in range(30):
        logger.warning("message %s %s", index, "x" * 40)

    # Close handlers so the final rotated segment is flushed and compressed.
    for handler in logger.handlers:
        handler.close()

    assert (tmp_path / "parishkit.log.1.gz").exists()
    assert (tmp_path / "parishkit.log.2.gz").exists()


def test_parse_log_level_rejects_unknown():
    """An unrecognized level name raises ValueError mentioning the bad level."""
    try:
        parse_log_level("NOPE")
    except ValueError as exc:
        assert "unknown log level" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_setup_logging_adds_mocked_slack_handler(monkeypatch, tmp_path):
    """A complete Slack config attaches a handler that emits to the channel.

    The real Slack handler is replaced with a fake so the test records emitted
    messages instead of contacting Slack.
    """
    token_file = tmp_path / "slack-token.txt"
    token_file.write_text("xoxb-test-token\n", encoding="utf-8")

    sent = []

    class FakeSlackHandler(logging.Handler):
        """Stand-in Slack handler that records (channel, message) tuples."""

        def __init__(self, *, token, channel):
            """Store the token and channel without opening any connection."""
            super().__init__()
            self.token = token
            self.channel = channel

        def emit(self, record):
            """Record the formatted record and its target channel."""
            sent.append((self.channel, self.format(record)))

    monkeypatch.setattr("parishkit.logging.SlackLogHandler", FakeSlackHandler)
    logger = setup_logging(
        logger_name="test.slack",
        slack_token_file=token_file,
        slack_channel="#alerts",
        slack_level="ERROR",
    )

    logger.error("problem")

    assert sent
    assert sent[0][0] == "#alerts"
    assert "ERROR test.slack: problem" in sent[0][1]
    assert not sent[0][1].startswith("{")


def test_setup_logging_rejects_partial_slack_config(tmp_path):
    """A Slack token without a channel is rejected with a ConfigError."""
    token_file = tmp_path / "slack-token.txt"
    token_file.write_text("xoxb-test-token\n", encoding="utf-8")

    try:
        setup_logging(
            logger_name="test.partial.slack",
            slack_token_file=token_file,
        )
    except ConfigError as exc:
        assert "both token file and channel" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_setup_logging_failure_keeps_existing_handlers(monkeypatch, tmp_path):
    """If reconfiguration fails partway, the logger keeps its prior handlers.

    setup_logging applies its handler set atomically, so a handler that raises
    during construction must not leave the logger half-reconfigured.
    """
    token_file = tmp_path / "slack-token.txt"
    token_file.write_text("xoxb-test-token\n", encoding="utf-8")

    logger = setup_logging(logger_name="test.atomic.setup")
    original_handlers = list(logger.handlers)

    class BrokenSlackHandler(logging.Handler):
        """Slack handler stub that fails on construction to trigger rollback."""

        def __init__(self, *, token, channel):
            raise RuntimeError("broken slack")

    monkeypatch.setattr("parishkit.logging.SlackLogHandler", BrokenSlackHandler)

    try:
        setup_logging(
            logger_name="test.atomic.setup",
            log_file=tmp_path / "new.log",
            slack_token_file=token_file,
            slack_channel="#alerts",
        )
    except RuntimeError as exc:
        assert "broken slack" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert logger.handlers == original_handlers
