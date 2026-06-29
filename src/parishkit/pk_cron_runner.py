"""Cron-friendly scheduled job runner support."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

from parishkit.cli import (
    add_common_arguments,
    default_config_dir,
    default_run_dir,
    resolve_common_options,
)
from parishkit.config import ConfigData, ConfigError, load_yaml_config
from parishkit.logging import log_extra, setup_logging

EXIT_SUCCESS = 0
EXIT_JOB_FAILED = 1
EXIT_CONFIG_ERROR = 2
EXIT_LOCKED = 3
EXIT_TIMEOUT = 4

DEFAULT_RUNNER_CONFIG = default_config_dir() / "runner.yaml"
_IMPORTED_DEFAULT_RUNNER_CONFIG = DEFAULT_RUNNER_CONFIG
DEFAULT_LOCK_FILE = default_run_dir() / "runner.lock"
SENSITIVE_WORDS = ("TOKEN", "SECRET", "PASSWORD", "PASS", "KEY", "CREDENTIAL", "AUTH")
MAX_CAPTURED_OUTPUT_BYTES = 200_000


@dataclass(frozen=True)
class LockConfig:
    """Settings controlling the single-instance runner lock.

    ``stale_after`` bounds how long a lock may persist before it is considered
    abandoned; ``stale_action`` chooses what to do when a stale lock is found.
    """

    path: Path = field(default_factory=lambda: default_run_dir() / "runner.lock")
    stale_after: timedelta | None = None
    stale_action: str = "exit-and-alert"


@dataclass(frozen=True)
class JobConfig:
    """A single command the runner can execute.

    ``command`` is an argv list (never a shell string). ``timeout`` is in
    seconds; ``None`` means wait indefinitely. Disabled jobs are skipped unless
    explicitly requested.
    """

    name: str
    command: list[str]
    enabled: bool = True
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout: float | None = None


@dataclass(frozen=True)
class RunnerConfig:
    """Fully resolved runner configuration for a single invocation.

    Aggregates the lock settings and the ordered list of jobs along with
    failure and Slack-notification policy. ``context`` is an optional label
    prepended to summary messages so multiple deployments are distinguishable.
    """

    lock: LockConfig = field(default_factory=LockConfig)
    jobs: list[JobConfig] = field(default_factory=list)
    stop_on_first_failure: bool = True
    notify_success: bool = False
    context: str | None = None
    include_output_in_slack: bool = False


@dataclass(frozen=True)
class JobResult:
    """Captured outcome of running one job.

    ``timed_out`` distinguishes a job killed for exceeding its timeout from one
    that merely returned a non-zero exit code, since the two are reported and
    exit-coded differently.
    """

    name: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        """Return True only if the job exited 0 and did not time out."""
        return self.returncode == 0 and not self.timed_out


def redacted_runner_config(config: RunnerConfig) -> dict[str, Any]:
    """Return a JSON-friendly runner config view with secrets redacted."""
    return {
        "lock": {
            "path": str(config.lock.path),
            "stale_after": (
                config.lock.stale_after.total_seconds()
                if config.lock.stale_after is not None
                else None
            ),
            "stale_action": config.lock.stale_action,
        },
        "jobs": [
            {
                "name": job.name,
                "command": _redacted_command(job.command),
                "enabled": job.enabled,
                "cwd": str(job.cwd) if job.cwd is not None else None,
                "env": _redacted_env(job.env),
                "timeout": job.timeout,
            }
            for job in config.jobs
        ],
        "stop_on_first_failure": config.stop_on_first_failure,
        "notify_success": config.notify_success,
        "context": config.context,
        "include_output_in_slack": config.include_output_in_slack,
    }


def _redacted_env(env: Mapping[str, str]) -> dict[str, str]:
    """Redact likely secret values from a job environment mapping."""
    return {
        key: "[redacted]" if _looks_sensitive(key) else value
        for key, value in env.items()
    }


def _redacted_command(command: Sequence[str]) -> list[str]:
    """Redact likely secret CLI argument values from a command list."""
    redacted: list[str] = []
    redact_next = False
    for token in command:
        if redact_next:
            redacted.append("[redacted]")
            redact_next = False
            continue
        if "=" in token:
            key, value = token.split("=", 1)
            redacted.append(f"{key}=[redacted]" if _looks_sensitive(key) else token)
            continue
        redacted.append(token)
        if token.startswith("-") and _looks_sensitive(token):
            redact_next = True
    return redacted


def _looks_sensitive(value: str) -> bool:
    """Return whether a key or flag name is likely to carry a secret."""
    upper = value.upper().replace("-", "_")
    return any(word in upper for word in SENSITIVE_WORDS)


class RunnerError(RuntimeError):
    """Base runner exception with a cron-friendly exit code."""

    exit_code = EXIT_JOB_FAILED


class LockUnavailable(RunnerError):
    """Raised when the runner lock is held or cannot be acquired safely."""

    exit_code = EXIT_LOCKED


class RunnerConfigError(RunnerError):
    """Raised for invalid runner configuration or job selection."""

    exit_code = EXIT_CONFIG_ERROR


def parse_duration(value: str | int | float | None) -> float | None:
    """Parse a duration into seconds.

    Accepts ``None``/empty (returns ``None``), a non-negative number (treated
    as seconds), or a string with a unit suffix: ``s``, ``m``, ``h``, or ``d``
    (e.g. ``"30m"``). Booleans and other types are rejected. String values must
    be a positive integer followed by a valid unit. Raises ``ConfigError`` on
    any malformed input.
    """
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ConfigError("duration must be a string or number")
    if isinstance(value, int | float):
        if value < 0:
            raise ConfigError("duration must be non-negative")
        return float(value)
    if not isinstance(value, str):
        raise ConfigError("duration must be a string or number")
    unit = value[-1]
    number = value[:-1]
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if unit not in multipliers or not number.isdigit() or int(number) < 1:
        raise ConfigError(f"invalid duration: {value}")
    return float(int(number) * multipliers[unit])


def _path(value: Any, key: str) -> Path | None:
    """Read a path config value."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string path")
    return Path(value).expanduser()


def _string_list(value: Any, key: str) -> list[str]:
    """Read a string list config value."""
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(v, str) for v in value)
    ):
        raise ConfigError(f"{key} must be a non-empty list of strings")
    return value


def _bool_value(value: Any, key: str, *, default: bool) -> bool:
    """Read a boolean config value."""
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean")
    return value


def _optional_string(value: Any, key: str) -> str | None:
    """Read an optional string config value."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    return value


def _choice(value: Any, key: str, choices: set[str], *, default: str) -> str:
    """Validate a scalar config value against allowed choices."""
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    if value not in choices:
        raise ConfigError(f"{key} must be one of: {', '.join(sorted(choices))}")
    return value


def load_runner_config(path: Path) -> RunnerConfig:
    """Load runner YAML configuration from disk."""
    data = load_yaml_config(path, required=True)
    return parse_runner_config(data, base_dir=path.parent)


def parse_runner_config(
    data: ConfigData,
    *,
    base_dir: Path | None = None,
) -> RunnerConfig:
    """Parse runner config.

    Each scalar is normalized at the boundary so the rest of the runner can
    work with typed configuration values.
    """
    lock_data = data.get("lock", {})
    if not isinstance(lock_data, dict):
        raise ConfigError("lock must be a mapping")
    runner_data = data.get("runner", {})
    if not isinstance(runner_data, dict):
        raise ConfigError("runner must be a mapping")
    slack_data = data.get("slack", {})
    if not isinstance(slack_data, dict):
        raise ConfigError("slack must be a mapping")

    lock_path = _path(lock_data.get("path"), "lock.path") or (
        default_run_dir() / "runner.lock"
    )
    # Relative paths are resolved against the config file's directory so a
    # config remains portable regardless of the process working directory.
    if base_dir and not lock_path.is_absolute():
        lock_path = base_dir / lock_path
    stale_after = parse_duration(lock_data.get("stale_after"))
    stale_action = _choice(
        lock_data.get("stale_action"),
        "lock.stale_action",
        {"exit-and-alert", "remove-and-continue", "fail-closed"},
        default="exit-and-alert",
    )
    runner_context = _optional_string(runner_data.get("context"), "runner.context")
    slack_context = _optional_string(slack_data.get("context"), "slack.context")

    jobs_raw = data.get("jobs", [])
    if not isinstance(jobs_raw, list):
        raise ConfigError("jobs must be a list")
    jobs = [_parse_job(job, base_dir=base_dir) for job in jobs_raw]
    _validate_unique_job_names(jobs)

    return RunnerConfig(
        lock=LockConfig(
            path=lock_path,
            stale_after=timedelta(seconds=stale_after) if stale_after else None,
            stale_action=stale_action,
        ),
        jobs=jobs,
        stop_on_first_failure=_bool_value(
            runner_data.get("stop_on_first_failure"),
            "runner.stop_on_first_failure",
            default=True,
        ),
        # Prefer the slack section but fall back to the runner section so older
        # configs that placed notify_success under runner: still work.
        notify_success=_bool_value(
            slack_data.get("notify_success", runner_data.get("notify_success")),
            "slack.notify_success",
            default=False,
        ),
        # runner.context wins over slack.context when both are present.
        context=runner_context or slack_context,
        include_output_in_slack=_bool_value(
            slack_data.get("include_output"),
            "slack.include_output",
            default=False,
        ),
    )


def _parse_job(raw: Any, *, base_dir: Path | None) -> JobConfig:
    """Parse one configured runner job."""
    if not isinstance(raw, dict):
        raise ConfigError("each job must be a mapping")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError("job.name must be a non-empty string")
    command = _string_list(raw.get("command"), f"job {name}.command")
    cwd = _path(raw.get("cwd"), f"job {name}.cwd")
    if base_dir and cwd is not None and not cwd.is_absolute():
        cwd = base_dir / cwd
    env = raw.get("env", {})
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ConfigError(f"job {name}.env must be a mapping of strings")
    return JobConfig(
        name=name,
        command=command,
        enabled=_bool_value(raw.get("enabled"), f"job {name}.enabled", default=True),
        cwd=cwd,
        env=env,
        timeout=parse_duration(raw.get("timeout")),
    )


def _validate_unique_job_names(jobs: list[JobConfig]) -> None:
    """Validate unique job names."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for job in jobs:
        if job.name in seen:
            duplicates.add(job.name)
        seen.add(job.name)
    if duplicates:
        raise ConfigError(f"duplicate job name(s): {', '.join(sorted(duplicates))}")


class LockFile:
    """Exclusive single-instance lock backed by an on-disk lock file.

    Used as a context manager around a runner invocation to guarantee that
    only one runner runs at a time. Each instance generates a unique token
    written into the lock metadata; release only removes the file when that
    token still matches, so a process never deletes a lock owned by a
    different, newer runner.
    """

    def __init__(
        self,
        config: LockConfig,
        *,
        command: list[str],
        timeout: float | None = None,
    ) -> None:
        """Prepare a lock for ``config.path`` with a fresh ownership token."""
        self.config = config
        self.command = command
        self.timeout = timeout
        self.path = config.path
        self._acquired = False
        self._token = uuid.uuid4().hex

    def __enter__(self) -> LockFile:
        """Acquire the lock on entry and return self."""
        self.acquire()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        """Release the lock on exit, whether or not the body raised."""
        self.release()

    def acquire(self) -> None:
        """Acquire the cron runner lock or raise ``LockUnavailable``.

        Creation uses ``O_CREAT | O_EXCL`` so the lock file is created
        atomically: if it already exists, ``_handle_existing_lock`` decides
        whether the lock is stale and may be removed. The whole sequence runs
        under ``_stale_recovery_guard`` so concurrent runners do not race while
        clearing a stale lock. A second ``FileExistsError`` after recovery means
        another process won the race, which is reported rather than overwritten.
        """
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        metadata = self._metadata()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._stale_recovery_guard():
                try:
                    fd = os.open(self.path, flags, 0o600)
                except FileExistsError:
                    # Lock present: drop it only if stale, then retry once.
                    self._handle_existing_lock()
                    try:
                        fd = os.open(self.path, flags, 0o600)
                    except FileExistsError as exc:
                        raise LockUnavailable(
                            "runner lock changed while recovering stale lock: "
                            f"{self.path}"
                        ) from exc
                self._write_metadata(fd, metadata)
        except OSError as exc:
            raise LockUnavailable(f"runner lock failed: {self.path}: {exc}") from exc

    def _write_metadata(self, fd: int, metadata: dict[str, Any]) -> None:
        """Write lock metadata to the open descriptor and register the lock.

        Takes ownership of ``fd``. The contents are flushed and fsynced so the
        token survives a crash. On write failure the partial lock file is
        removed before re-raising. On success the lock is appended to
        ``_ACTIVE_LOCKS`` so signal handlers can release it during shutdown.
        """
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
                json.dump(metadata, lock_file, indent=2)
                lock_file.flush()
                os.fsync(lock_file.fileno())
        except OSError:
            with suppress(FileNotFoundError):
                self.path.unlink()
            raise
        self._acquired = True
        _ACTIVE_LOCKS.append(self)

    @contextmanager
    def _stale_recovery_guard(self) -> Iterator[None]:
        """Serialize stale-lock recovery between runner processes.

        Holds an exclusive ``flock`` on a sibling ``.recovery`` file for the
        duration of the acquire attempt. The advisory flock (separate from the
        O_EXCL lock file itself) prevents two runners from simultaneously
        deciding the same stale lock can be removed and both proceeding.
        """
        guard_path = self.path.with_name(f"{self.path.name}.recovery")
        with guard_path.open("a+", encoding="utf-8") as guard_file:
            fcntl.flock(guard_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(guard_file.fileno(), fcntl.LOCK_UN)

    def release(self) -> None:
        """Release this runner's lock file if we still own it.

        Safe to call more than once. The on-disk token is re-read and compared
        to ours before unlinking so we never delete a lock that a different
        runner has since taken over (e.g. after stale recovery elsewhere).
        """
        if self._acquired:
            metadata = read_lock_metadata(self.path)
            if metadata.get("token") == self._token:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    logging.getLogger("parishkit.pk_cron_runner").warning(
                        "failed to remove runner lock %s: %s", self.path, exc
                    )
            self._acquired = False
            if self in _ACTIVE_LOCKS:
                _ACTIVE_LOCKS.remove(self)

    def _metadata(self) -> dict[str, Any]:
        """Build lock metadata for the current process."""
        return {
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "command": _redacted_command(self.command),
            "start_time": datetime.now(UTC).isoformat(),
            "timeout": self.timeout,
            "token": self._token,
        }

    def _handle_existing_lock(self) -> None:
        """Decide what to do about a pre-existing lock file.

        Reads the existing metadata and raises ``LockUnavailable`` if the lock
        is unreadable or still active. If it is stale, the configured
        ``stale_action`` governs the outcome: ``remove-and-continue`` deletes it
        (after re-reading to confirm it has not changed) so acquisition can
        retry; ``fail-closed`` and the default both refuse to proceed.
        """
        metadata = read_lock_metadata(self.path)
        if not metadata:
            raise LockUnavailable(f"runner lock metadata is unavailable: {self.path}")
        stale = is_lock_stale(metadata, self.config.stale_after)
        if not stale:
            raise LockUnavailable(f"runner lock is active: {self.path}")
        if self.config.stale_action == "remove-and-continue":
            current_metadata = read_lock_metadata(self.path)
            if current_metadata != metadata:
                raise LockUnavailable(
                    f"runner lock changed while checking stale lock: {self.path}"
                )
            with suppress(FileNotFoundError):
                self.path.unlink()
            return
        if self.config.stale_action == "fail-closed":
            raise LockUnavailable(f"runner lock is stale; failing closed: {self.path}")
        raise LockUnavailable(f"runner lock is stale: {self.path}")


def read_lock_metadata(path: Path) -> dict[str, Any]:
    """Read lock metadata, returning an empty dict if missing or malformed.

    A missing file or corrupt JSON yields ``{}`` so callers can treat an
    unreadable lock uniformly rather than crashing.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def is_lock_stale(metadata: dict[str, Any], stale_after: timedelta | None) -> bool:
    """Report whether a lock has aged past ``stale_after``.

    Returns ``False`` when staleness checking is disabled (``stale_after`` is
    ``None``). A missing or unparseable ``start_time`` is treated as stale.
    Naive timestamps are assumed to be UTC for the age comparison.
    """
    if stale_after is None:
        return False
    raw_start = metadata.get("start_time")
    if not isinstance(raw_start, str):
        return True
    try:
        start = datetime.fromisoformat(raw_start)
    except ValueError:
        return True
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    return datetime.now(UTC) - start > stale_after


def run_job(job: JobConfig) -> JobResult:
    """Run one job to completion and capture its result.

    The job's ``env`` is layered on top of the current environment, output is
    spooled to temporary files and read back with a size limit, and the child
    runs in its own session/process group (``start_new_session=True``) so a
    timeout can signal the whole group rather than orphaning grandchildren. The
    steps are kept explicit so operational behavior stays easy to audit and
    test. Returns a ``JobResult`` for every outcome: a failed spawn yields
    ``EXIT_JOB_FAILED`` and a timeout yields ``EXIT_TIMEOUT`` with
    ``timed_out=True``; no exception escapes.
    """
    env = os.environ.copy()
    env.update(job.env)
    with (
        tempfile.TemporaryFile() as stdout_file,
        tempfile.TemporaryFile() as stderr_file,
    ):
        try:
            process = subprocess.Popen(
                job.command,
                cwd=job.cwd,
                env=env,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
        except OSError as exc:
            # Command could not be launched at all (e.g. missing executable).
            return JobResult(
                name=job.name,
                returncode=EXIT_JOB_FAILED,
                stdout="",
                stderr=str(exc),
            )
        # Track the live process so signal handlers can tear it down on shutdown.
        _ACTIVE_PROCESSES.append(process)
        try:
            process.wait(timeout=job.timeout)
        except subprocess.TimeoutExpired:
            _terminate_process(process)
            process.wait()
            return JobResult(
                name=job.name,
                returncode=EXIT_TIMEOUT,
                stdout=_read_captured_output(stdout_file),
                stderr=_read_captured_output(stderr_file),
                timed_out=True,
            )
        finally:
            if process in _ACTIVE_PROCESSES:
                _ACTIVE_PROCESSES.remove(process)
        return JobResult(
            name=job.name,
            returncode=process.returncode,
            stdout=_read_captured_output(stdout_file),
            stderr=_read_captured_output(stderr_file),
        )


def _read_captured_output(output_file: Any) -> str:
    """Read a bounded tail of child output from a temporary file."""
    output_file.flush()
    output_file.seek(0, os.SEEK_END)
    size = output_file.tell()
    start = max(0, size - MAX_CAPTURED_OUTPUT_BYTES)
    output_file.seek(start)
    data = output_file.read()
    text = data.decode("utf-8", errors="replace")
    if start:
        return "... output truncated ...\n" + text
    return text


def run_jobs(
    config: RunnerConfig,
    *,
    selected_jobs: list[str] | None = None,
    include_disabled: bool = False,
    logger: logging.Logger | None = None,
) -> tuple[int, list[JobResult]]:
    """Run selected cron jobs and collect their results."""
    logger = logger or logging.getLogger("parishkit.pk_cron_runner")
    jobs = select_jobs(config.jobs, selected_jobs, include_disabled=include_disabled)
    if not jobs:
        raise RunnerConfigError("no jobs selected")
    results: list[JobResult] = []
    for job in jobs:
        logger.info(
            "running job %s: %s",
            job.name,
            shlex.join(_redacted_command(job.command)),
        )
        result = run_job(job)
        results.append(result)
        if not result.ok:
            logger.error("job %s failed with exit code %s", job.name, result.returncode)
            if config.stop_on_first_failure:
                break
    return _results_exit_code(results), results


def _results_exit_code(results: list[JobResult]) -> int:
    """Return the process exit code for runner results."""
    if any(result.timed_out for result in results):
        return EXIT_TIMEOUT
    if any(not result.ok for result in results):
        return EXIT_JOB_FAILED
    return EXIT_SUCCESS


def select_jobs(
    jobs: list[JobConfig],
    selected_jobs: list[str] | None,
    *,
    include_disabled: bool = False,
) -> list[JobConfig]:
    """Select which jobs to run.

    With no ``selected_jobs`` every job is returned in config order; disabled
    jobs are dropped unless ``include_disabled`` is set. When names are given,
    unknown names raise ``RunnerConfigError`` and the result follows the
    requested order (not config order). Requested-but-disabled jobs are still
    filtered out unless ``include_disabled`` is set.
    """
    if selected_jobs:
        by_name = {job.name: job for job in jobs}
        missing = sorted(set(selected_jobs) - set(by_name))
        if missing:
            raise RunnerConfigError(f"unknown job(s): {', '.join(missing)}")
        # The inner `for job in [by_name[name]]` binds each requested name to
        # its JobConfig so the trailing filter can drop disabled ones while
        # preserving the user-requested ordering.
        return [
            job
            for name in selected_jobs
            for job in [by_name[name]]
            if include_disabled or job.enabled
        ]
    return [job for job in jobs if include_disabled or job.enabled]


def _single_command_config(args: argparse.Namespace) -> RunnerConfig:
    """Build runner config for direct command mode."""
    if not args.command:
        raise ConfigError("--command requires at least one command argument")
    lock_path = args.lock_file or default_run_dir() / "runner.lock"
    stale_after = (
        timedelta(seconds=parse_duration(args.stale_after))
        if args.stale_after
        else None
    )
    return RunnerConfig(
        lock=LockConfig(
            path=lock_path,
            stale_after=stale_after,
            stale_action=args.stale_action or "exit-and-alert",
        ),
        jobs=[
            JobConfig(
                name="command",
                command=args.command,
                timeout=parse_duration(args.timeout),
            )
        ],
        stop_on_first_failure=True,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(prog="pk-cron-runner")
    parser.add_argument(
        "--version",
        action="store_true",
        help="show that the console entry point is installed",
    )
    add_common_arguments(parser)
    parser.add_argument("jobs", nargs="*", help="configured job names to run")
    parser.add_argument("--include-disabled", action="store_true")
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument("--lock-file", type=Path)
    parser.add_argument("--stale-after")
    parser.add_argument(
        "--stale-action",
        choices=["exit-and-alert", "remove-and-continue", "fail-closed"],
    )
    parser.add_argument("--timeout")
    parser.add_argument(
        "--command",
        nargs=argparse.REMAINDER,
        help="run a single command when no runner config is available",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the cron runner CLI and return a process exit code.

    Parses arguments, sets up logging, resolves the effective configuration
    (YAML or single ``--command`` mode) with CLI overrides applied, then
    executes the selected jobs while holding the single-instance lock and
    custom signal handlers. All user-facing error handling lives here: known
    runner and config failures are logged and mapped to their dedicated exit
    codes rather than raising, so cron sees a meaningful status.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"pk-cron-runner {version('parishkit')}")
        return EXIT_SUCCESS
    try:
        if args.command is not None and args.config:
            raise ConfigError("--command cannot be combined with --config")
        effective_config = _effective_config_path(args)
        args.config = effective_config
        options = resolve_common_options(args)
        try:
            logger = setup_logging(
                verbose=options.verbose,
                debug=options.debug,
                log_file=options.log_file,
                log_dir=options.log_dir,
                logger_name="parishkit.pk_cron_runner",
                slack_token_file=options.slack_token_file,
                slack_channel=options.slack_channel,
                slack_level=options.slack_log_level,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ConfigError(f"logging setup failed: {exc}") from exc
        runner_config = _load_or_build_config(args, effective_config)
        runner_config = _apply_cli_overrides(runner_config, args)
        # Rebuild the frozen config with stop_on_first_failure cleared; all
        # other fields are carried over unchanged.
        if args.continue_on_failure:
            runner_config = RunnerConfig(
                lock=runner_config.lock,
                jobs=runner_config.jobs,
                stop_on_first_failure=False,
                notify_success=runner_config.notify_success,
                context=runner_config.context,
                include_output_in_slack=runner_config.include_output_in_slack,
            )
        logger.info(
            "Configured %s runner job(s); selected=%s include_disabled=%s",
            len(runner_config.jobs),
            args.jobs or "all",
            args.include_disabled,
        )
        logger.debug(
            "Runner config: %s job(s), stop_on_first_failure=%s, notify_success=%s",
            len(runner_config.jobs),
            runner_config.stop_on_first_failure,
            runner_config.notify_success,
            extra=log_extra(redacted_runner_config(runner_config)),
        )
        with (
            _signal_handlers(),
            LockFile(
                runner_config.lock,
                command=sys.argv if argv is None else argv,
            ),
        ):
            exit_code, results = run_jobs(
                runner_config,
                selected_jobs=args.jobs or None,
                include_disabled=args.include_disabled,
                logger=logger,
            )
        _log_summary(logger, runner_config, exit_code, results)
        return exit_code
    except RunnerError as exc:
        logging.getLogger("parishkit.pk_cron_runner").critical("%s", exc)
        return exc.exit_code
    except ConfigError as exc:
        logging.getLogger("parishkit.pk_cron_runner").critical(
            "configuration error: %s", exc
        )
        return EXIT_CONFIG_ERROR


def _load_or_build_config(
    args: argparse.Namespace,
    config_path: Path | None,
) -> RunnerConfig:
    """Load YAML config or build command-mode config."""
    if config_path:
        return load_runner_config(config_path)
    if args.command is not None:
        return _single_command_config(args)
    default_config = _default_runner_config_path()
    raise RunnerConfigError(
        f"no runner config found at {default_config}; provide --config or --command"
    )


def _apply_cli_overrides(
    config: RunnerConfig,
    args: argparse.Namespace,
) -> RunnerConfig:
    """Override lock settings and per-job timeout from CLI flags.

    Each lock-related flag falls back to the existing config value when not
    given. A ``--timeout`` flag, if present, replaces the timeout on *every*
    job; otherwise the configured per-job timeouts are left untouched.
    """
    lock_path = args.lock_file or config.lock.path
    stale_after = (
        timedelta(seconds=parse_duration(args.stale_after))
        if args.stale_after
        else config.lock.stale_after
    )
    stale_action = args.stale_action or config.lock.stale_action
    timeout = parse_duration(args.timeout) if args.timeout else None
    # A CLI timeout is a blanket override applied uniformly to all jobs.
    jobs = (
        [replace(job, timeout=timeout) for job in config.jobs]
        if timeout is not None
        else config.jobs
    )
    return RunnerConfig(
        lock=LockConfig(
            path=lock_path,
            stale_after=stale_after,
            stale_action=stale_action,
        ),
        jobs=jobs,
        stop_on_first_failure=config.stop_on_first_failure,
        notify_success=config.notify_success,
        context=config.context,
        include_output_in_slack=config.include_output_in_slack,
    )


def _effective_config_path(args: argparse.Namespace) -> Path | None:
    """Return the config path that should be reported."""
    if args.config:
        return Path(args.config).expanduser().resolve()
    if args.command is not None:
        return None
    default_config = _default_runner_config_path()
    if default_config.exists():
        return default_config
    return None


def _default_runner_config_path() -> Path:
    """Return the current default runner config, preserving test monkeypatches."""
    if DEFAULT_RUNNER_CONFIG != _IMPORTED_DEFAULT_RUNNER_CONFIG:
        return DEFAULT_RUNNER_CONFIG
    return default_config_dir() / "runner.yaml"


def _log_summary(
    logger: logging.Logger,
    config: RunnerConfig,
    exit_code: int,
    results: list[JobResult],
) -> None:
    """Log and optionally notify a runner summary."""
    context = f"{config.context}: " if config.context else ""
    if exit_code == EXIT_SUCCESS:
        message = f"{context}runner completed successfully ({len(results)} job(s))"
        if config.notify_success:
            logger.critical(message)
        else:
            logger.info(message)
        return
    logger.critical("%s%s", context, _failure_summary(exit_code, results, config))


def _failure_summary(
    exit_code: int,
    results: list[JobResult],
    config: RunnerConfig,
) -> str:
    """Build the cron runner failure summary message."""
    failed = [result for result in results if not result.ok]
    lines = [f"runner failed ({len(results)} job(s), exit {exit_code})"]
    for result in failed:
        status = "timeout" if result.timed_out else f"exit {result.returncode}"
        lines.append(f"- {result.name}: {status}")
        if config.include_output_in_slack:
            output = _bounded_output(result)
            if output:
                lines.append(output)
    return "\n".join(lines)


def _bounded_output(result: JobResult, limit: int = 1500) -> str:
    """Return output trimmed to the configured display limit."""
    parts = []
    if result.stderr:
        parts.append(f"stderr:\n{result.stderr.strip()}")
    if result.stdout:
        parts.append(f"stdout:\n{result.stdout.strip()}")
    output = "\n".join(parts)
    if len(output) > limit:
        return output[:limit] + "\n... output truncated ..."
    return output


_ACTIVE_LOCKS: list[LockFile] = []
_ACTIVE_PROCESSES: list[subprocess.Popen[str]] = []


def _terminate_process(process: subprocess.Popen[str]) -> None:
    """Stop a child process and its descendants, escalating if needed.

    Sends ``SIGTERM`` to the child's process group (so grandchildren spawned by
    the job are also signalled), waits up to five seconds, then escalates to
    ``SIGKILL`` if it is still alive. ``killpg`` is preferred but falls back to
    signalling just the child if the group call fails. Already-exited processes
    and lost PIDs are handled quietly so this is safe to call unconditionally.
    """
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            process.kill()
        process.wait()


@contextmanager
def _signal_handlers() -> Iterator[None]:
    """Install SIGTERM/SIGINT handlers that clean up before exiting.

    While active, a delivered signal terminates every tracked child process and
    releases every held lock, then raises ``SystemExit(128 + signum)`` (the
    shell convention for signal-caused exits). The previous handlers are
    restored on exit so the override is scoped to the runner invocation.
    """
    previous_handlers = {
        signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)
    }

    def handle_signal(signum: int, _frame: object) -> None:
        """Handle a shutdown signal for a child process."""
        for process in list(_ACTIVE_PROCESSES):
            _terminate_process(process)
        for lock in list(_ACTIVE_LOCKS):
            lock.release()
        raise SystemExit(128 + signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, handle_signal)
    try:
        yield
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
