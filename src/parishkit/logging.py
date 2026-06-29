"""Shared logging setup, including optional external notifications."""

from __future__ import annotations

import gzip
import json
import logging
import shutil
from collections.abc import Mapping, Sequence, Set
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from parishkit.config import ConfigError

DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DEFAULT_SLACK_LEVEL = logging.CRITICAL
DEFAULT_MAX_BYTES = 50_000_000
DEFAULT_BACKUP_COUNT = 50
STRUCTURED_EXTRA_FIELD = "extra"


def log_extra(value: Any) -> dict[str, Any]:
    """Return a logging ``extra`` dict carrying structured JSONL context."""
    return {STRUCTURED_EXTRA_FIELD: value}


def _jsonable(value: Any) -> Any:
    """Convert common Python objects into values accepted by ``json.dumps``."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Set):
        return [_jsonable(item) for item in sorted(value, key=repr)]
    if isinstance(value, Sequence):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return repr(value)


class JsonLogFormatter(logging.Formatter):
    """Format file log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        """Return a JSON representation of ``record`` suitable for JSONL logs."""
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        if hasattr(record, STRUCTURED_EXTRA_FIELD):
            payload[STRUCTURED_EXTRA_FIELD] = _jsonable(
                getattr(record, STRUCTURED_EXTRA_FIELD)
            )
        return json.dumps(payload)


class CompressingRotatingFileHandler(RotatingFileHandler):
    """Rotating file handler that gzips rotated log files."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Build a rotating handler that names and compresses rollovers.

        Accepts the same arguments as ``RotatingFileHandler`` and installs a
        namer/rotator pair so each rotated file gains a ``.gz`` suffix and is
        written through gzip rather than copied verbatim.
        """
        super().__init__(*args, **kwargs)
        self.namer = lambda name: f"{name}.gz"
        self.rotator = self._gzip_rotator

    @staticmethod
    def _gzip_rotator(source: str, dest: str) -> None:
        """Compress a rotated log file with gzip."""
        with Path(source).open("rb") as source_file, gzip.open(dest, "wb") as target:
            shutil.copyfileobj(source_file, target)
        Path(source).unlink()


class SlackLogHandler(logging.Handler):
    """Send log records to Slack using a bot token."""

    def __init__(self, *, token: str, channel: str) -> None:
        """Create a handler that posts records to ``channel`` via ``token``.

        ``slack_sdk`` is imported lazily so installations that never enable
        Slack notifications do not need the optional dependency; a missing
        import is surfaced as a clear ``ConfigError``.
        """
        super().__init__()
        self.channel = channel
        try:
            from slack_sdk import WebClient
        except ImportError as exc:  # pragma: no cover - exercised by config users
            raise ConfigError("slack_sdk is required for Slack logging") from exc
        self.client = WebClient(token=token)

    def emit(self, record: logging.LogRecord) -> None:
        """Post a formatted record to Slack, swallowing delivery failures.

        Any error while contacting Slack is routed through ``handleError`` so a
        notification problem never propagates back into the code that logged
        the original message.
        """
        try:
            self.client.chat_postMessage(channel=self.channel, text=self.format(record))
        except Exception:  # pragma: no cover - logging must not mask original errors
            self.handleError(record)


def _read_secret_file(path: Path) -> str:
    """Read a secret (such as a token) from ``path``, trimming whitespace.

    Trailing newlines are common in files written by editors or shells, so the
    contents are stripped to avoid sending stray whitespace as part of a token.
    """
    return path.expanduser().read_text(encoding="utf-8").strip()


def parse_log_level(level: str | int | None, *, default: int = logging.INFO) -> int:
    """Normalize a logging level into its integer constant.

    Accepts a level name (case-insensitive, e.g. ``"info"``), an already-numeric
    level, or ``None`` to fall back to ``default``. Raises ``ValueError`` for an
    unrecognized name.
    """
    if level is None:
        return default
    if isinstance(level, int):
        return level
    normalized = level.upper()
    if normalized not in logging._nameToLevel:
        raise ValueError(f"unknown log level: {level}")
    return logging._nameToLevel[normalized]


def setup_logging(
    *,
    verbose: bool = False,
    debug: bool = False,
    log_file: str | Path | None = None,
    log_dir: str | Path | None = None,
    logger_name: str | None = None,
    rotate: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    slack_token_file: str | Path | None = None,
    slack_channel: str | None = None,
    slack_level: str | int = DEFAULT_SLACK_LEVEL,
) -> logging.Logger:
    """Configure console, optional file, and optional Slack logging.

    Builds a console handler plus, when requested, a rotating (optionally
    gzip-compressing) file handler and a Slack handler, then atomically swaps
    them onto the named logger. Verbosity is layered: ``debug`` outranks
    ``verbose``, which outranks the default warning-only console output.

    Args:
        verbose: Lower the console threshold to ``INFO``.
        debug: Lower both console and file thresholds to ``DEBUG``.
        log_file: Explicit log file path; takes precedence over ``log_dir``.
        log_dir: Directory in which to create ``parishkit.log`` when no
            explicit ``log_file`` is given.
        logger_name: Name of the logger to configure (root when ``None``).
        rotate: Use size-based rotation with gzip compression of old files.
        max_bytes/backup_count: Rotation thresholds passed to the handler.
        slack_token_file/slack_channel: Both required together to enable Slack
            notifications; supplying only one is a configuration error.
        slack_level: Minimum level that is forwarded to Slack.

    Returns the configured logger. Raises ``ConfigError`` if exactly one of the
    Slack options is provided.
    """

    logger = logging.getLogger(logger_name)
    package_logger = (
        logging.getLogger("parishkit")
        if logger_name is not None and logger_name.startswith("parishkit.")
        else None
    )
    # Slack delivery needs both a token and a destination; reject a half
    # configuration early rather than silently dropping notifications.
    if bool(slack_token_file) != bool(slack_channel):
        raise ConfigError("Slack logging requires both token file and channel")

    console_level = (
        logging.DEBUG if debug else logging.INFO if verbose else logging.WARNING
    )
    text_formatter = logging.Formatter(DEFAULT_LOG_FORMAT)
    file_formatter = JsonLogFormatter()
    new_handlers: list[logging.Handler] = []

    try:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(console_level)
        console_handler.setFormatter(text_formatter)
        new_handlers.append(console_handler)

        chosen_log_file: Path | None = Path(log_file).expanduser() if log_file else None
        if chosen_log_file is None and log_dir is not None:
            chosen_log_file = Path(log_dir).expanduser() / "parishkit.log"

        if chosen_log_file is not None:
            chosen_log_file.parent.mkdir(parents=True, exist_ok=True)
            if rotate:
                file_handler: logging.Handler = CompressingRotatingFileHandler(
                    chosen_log_file,
                    maxBytes=max_bytes,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
            else:
                file_handler = logging.FileHandler(chosen_log_file, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
            file_handler.setFormatter(file_formatter)
            new_handlers.append(file_handler)

        if slack_token_file and slack_channel:
            token = _read_secret_file(Path(slack_token_file))
            slack_handler = SlackLogHandler(token=token, channel=slack_channel)
            slack_handler.setLevel(
                parse_log_level(slack_level, default=DEFAULT_SLACK_LEVEL)
            )
            slack_handler.setFormatter(text_formatter)
            new_handlers.append(slack_handler)
    except Exception:
        # If any handler fails to construct, close the ones already built so we
        # do not leak open file descriptors before re-raising.
        for handler in new_handlers:
            handler.close()
        raise

    # Swap handlers only after all new ones are ready, so a failure above leaves
    # the previously configured logger untouched.
    configured_loggers = [logger]
    if package_logger is not None:
        configured_loggers.append(package_logger)
    old_handlers = set()
    for target_logger in configured_loggers:
        for handler in list(target_logger.handlers):
            target_logger.removeHandler(handler)
            old_handlers.add(handler)
    for handler in old_handlers:
        handler.close()
    # Keep the logger itself permissive; per-handler levels do the real
    # filtering, and propagation is disabled to avoid duplicate root output.
    for target_logger in configured_loggers:
        target_logger.setLevel(logging.DEBUG)
        target_logger.propagate = False
    for handler in new_handlers:
        logger.addHandler(handler)
        if package_logger is not None:
            package_logger.addHandler(handler)

    return logger


def describe_handlers(logger: logging.Logger) -> list[dict[str, Any]]:
    """Describe configured logging handlers for diagnostics."""
    return [
        {"type": handler.__class__.__name__, "level": handler.level}
        for handler in logger.handlers
    ]
