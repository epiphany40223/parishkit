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
        super().__init__(*args, **kwargs)
        self.namer = lambda name: f"{name}.gz"
        self.rotator = self._gzip_rotator

    @staticmethod
    def _gzip_rotator(source: str, dest: str) -> None:
        with Path(source).open("rb") as source_file, gzip.open(dest, "wb") as target:
            shutil.copyfileobj(source_file, target)
        Path(source).unlink()


class SlackLogHandler(logging.Handler):
    """Send log records to Slack using a bot token."""

    def __init__(self, *, token: str, channel: str) -> None:
        super().__init__()
        self.channel = channel
        try:
            from slack_sdk import WebClient
        except ImportError as exc:  # pragma: no cover - exercised by config users
            raise RuntimeError("slack_sdk is required for Slack logging") from exc
        self.client = WebClient(token=token)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.client.chat_postMessage(channel=self.channel, text=self.format(record))
        except Exception:  # pragma: no cover - logging must not mask original errors
            self.handleError(record)


def _read_secret_file(path: Path) -> str:
    return path.expanduser().read_text(encoding="utf-8").strip()


def parse_log_level(level: str | int | None, *, default: int = logging.INFO) -> int:
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
    """Configure console, optional file, and optional Slack logging."""

    logger = logging.getLogger(logger_name)
    if bool(slack_token_file) != bool(slack_channel):
        raise ValueError("Slack logging requires both token file and channel")

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
        for handler in new_handlers:
            handler.close()
        raise

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in new_handlers:
        logger.addHandler(handler)

    return logger


def describe_handlers(logger: logging.Logger) -> list[dict[str, Any]]:
    return [
        {"type": handler.__class__.__name__, "level": handler.level}
        for handler in logger.handlers
    ]
