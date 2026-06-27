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
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any

from parishkit.cli import (
    DEFAULT_CONFIG_DIR,
    DEFAULT_RUN_DIR,
    add_common_arguments,
    resolve_common_options,
)
from parishkit.config import ConfigData, ConfigError, load_yaml_config
from parishkit.logging import setup_logging

EXIT_SUCCESS = 0
EXIT_JOB_FAILED = 1
EXIT_CONFIG_ERROR = 2
EXIT_LOCKED = 3
EXIT_TIMEOUT = 4

DEFAULT_RUNNER_CONFIG = DEFAULT_CONFIG_DIR / "runner.yaml"
DEFAULT_LOCK_FILE = DEFAULT_RUN_DIR / "runner.lock"


@dataclass(frozen=True)
class LockConfig:
    path: Path = DEFAULT_LOCK_FILE
    stale_after: timedelta | None = None
    stale_action: str = "exit-and-alert"


@dataclass(frozen=True)
class JobConfig:
    name: str
    command: list[str]
    enabled: bool = True
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    timeout: float | None = None


@dataclass(frozen=True)
class RunnerConfig:
    lock: LockConfig = field(default_factory=LockConfig)
    jobs: list[JobConfig] = field(default_factory=list)
    stop_on_first_failure: bool = True
    notify_success: bool = False
    context: str | None = None
    include_output_in_slack: bool = False


@dataclass(frozen=True)
class JobResult:
    name: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class RunnerError(RuntimeError):
    """Base runner exception with a cron-friendly exit code."""

    exit_code = EXIT_JOB_FAILED


class LockUnavailable(RunnerError):
    exit_code = EXIT_LOCKED


class RunnerConfigError(RunnerError):
    exit_code = EXIT_CONFIG_ERROR


def parse_duration(value: str | int | float | None) -> float | None:
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
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string path")
    return Path(value).expanduser()


def _string_list(value: Any, key: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(v, str) for v in value)
    ):
        raise ConfigError(f"{key} must be a non-empty list of strings")
    return value


def _bool_value(value: Any, key: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean")
    return value


def _optional_string(value: Any, key: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    return value


def _choice(value: Any, key: str, choices: set[str], *, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    if value not in choices:
        raise ConfigError(f"{key} must be one of: {', '.join(sorted(choices))}")
    return value


def load_runner_config(path: Path) -> RunnerConfig:
    data = load_yaml_config(path, required=True)
    return parse_runner_config(data, base_dir=path.parent)


def parse_runner_config(
    data: ConfigData,
    *,
    base_dir: Path | None = None,
) -> RunnerConfig:
    lock_data = data.get("lock", {})
    if not isinstance(lock_data, dict):
        raise ConfigError("lock must be a mapping")
    runner_data = data.get("runner", {})
    if not isinstance(runner_data, dict):
        raise ConfigError("runner must be a mapping")
    slack_data = data.get("slack", {})
    if not isinstance(slack_data, dict):
        raise ConfigError("slack must be a mapping")

    lock_path = _path(lock_data.get("path"), "lock.path") or DEFAULT_LOCK_FILE
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
        notify_success=_bool_value(
            slack_data.get("notify_success", runner_data.get("notify_success")),
            "slack.notify_success",
            default=False,
        ),
        context=runner_context or slack_context,
        include_output_in_slack=_bool_value(
            slack_data.get("include_output"),
            "slack.include_output",
            default=False,
        ),
    )


def _parse_job(raw: Any, *, base_dir: Path | None) -> JobConfig:
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
    seen: set[str] = set()
    duplicates: set[str] = set()
    for job in jobs:
        if job.name in seen:
            duplicates.add(job.name)
        seen.add(job.name)
    if duplicates:
        raise ConfigError(f"duplicate job name(s): {', '.join(sorted(duplicates))}")


class LockFile:
    def __init__(
        self,
        config: LockConfig,
        *,
        command: list[str],
        timeout: float | None = None,
    ) -> None:
        self.config = config
        self.command = command
        self.timeout = timeout
        self.path = config.path
        self._acquired = False
        self._token = uuid.uuid4().hex

    def __enter__(self) -> LockFile:
        self.acquire()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.release()

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        metadata = self._metadata()
        with self._stale_recovery_guard():
            try:
                fd = os.open(self.path, flags, 0o644)
            except FileExistsError:
                self._handle_existing_lock()
                try:
                    fd = os.open(self.path, flags, 0o644)
                except FileExistsError as exc:
                    raise LockUnavailable(
                        f"runner lock changed while recovering stale lock: {self.path}"
                    ) from exc
            self._write_metadata(fd, metadata)

    def _write_metadata(self, fd: int, metadata: dict[str, Any]) -> None:
        with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
            json.dump(metadata, lock_file, indent=2)
            lock_file.flush()
            os.fsync(lock_file.fileno())
        self._acquired = True
        _ACTIVE_LOCKS.append(self)

    @contextmanager
    def _stale_recovery_guard(self) -> Iterator[None]:
        guard_path = self.path.with_name(f"{self.path.name}.recovery")
        with guard_path.open("a+", encoding="utf-8") as guard_file:
            fcntl.flock(guard_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(guard_file.fileno(), fcntl.LOCK_UN)

    def release(self) -> None:
        if self._acquired:
            metadata = read_lock_metadata(self.path)
            if metadata.get("token") == self._token:
                self.path.unlink()
            self._acquired = False
            if self in _ACTIVE_LOCKS:
                _ACTIVE_LOCKS.remove(self)

    def _metadata(self) -> dict[str, Any]:
        return {
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "command": self.command,
            "start_time": datetime.now(UTC).isoformat(),
            "timeout": self.timeout,
            "token": self._token,
        }

    def _handle_existing_lock(self) -> None:
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
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def is_lock_stale(metadata: dict[str, Any], stale_after: timedelta | None) -> bool:
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
    env = os.environ.copy()
    env.update(job.env)
    try:
        process = subprocess.Popen(
            job.command,
            cwd=job.cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        return JobResult(
            name=job.name,
            returncode=EXIT_JOB_FAILED,
            stdout="",
            stderr=str(exc),
        )
    _ACTIVE_PROCESSES.append(process)
    try:
        stdout, stderr = process.communicate(timeout=job.timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate_process(process)
        stdout, stderr = process.communicate()
        return JobResult(
            name=job.name,
            returncode=EXIT_TIMEOUT,
            stdout=stdout or exc.stdout or "",
            stderr=stderr or exc.stderr or "",
            timed_out=True,
        )
    finally:
        if process in _ACTIVE_PROCESSES:
            _ACTIVE_PROCESSES.remove(process)
    return JobResult(
        name=job.name,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def run_jobs(
    config: RunnerConfig,
    *,
    selected_jobs: list[str] | None = None,
    include_disabled: bool = False,
    logger: logging.Logger | None = None,
) -> tuple[int, list[JobResult]]:
    logger = logger or logging.getLogger("parishkit.runner")
    jobs = select_jobs(config.jobs, selected_jobs, include_disabled=include_disabled)
    if not jobs:
        raise RunnerConfigError("no jobs selected")
    results: list[JobResult] = []
    for job in jobs:
        logger.info("running job %s: %s", job.name, shlex.join(job.command))
        result = run_job(job)
        results.append(result)
        if not result.ok:
            logger.error("job %s failed with exit code %s", job.name, result.returncode)
            if config.stop_on_first_failure:
                break
    return _results_exit_code(results), results


def _results_exit_code(results: list[JobResult]) -> int:
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
    if selected_jobs:
        by_name = {job.name: job for job in jobs}
        missing = sorted(set(selected_jobs) - set(by_name))
        if missing:
            raise RunnerConfigError(f"unknown job(s): {', '.join(missing)}")
        return [
            job
            for name in selected_jobs
            for job in [by_name[name]]
            if include_disabled or job.enabled
        ]
    return [job for job in jobs if include_disabled or job.enabled]


def _single_command_config(args: argparse.Namespace) -> RunnerConfig:
    if not args.command:
        raise ConfigError("--command requires at least one command argument")
    lock_path = args.lock_file or DEFAULT_LOCK_FILE
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
    parser = argparse.ArgumentParser(prog="parishkit-run")
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
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"parishkit-run {version('parishkit')}")
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
                logger_name="parishkit.runner",
                slack_token_file=options.slack_token_file,
                slack_channel=options.slack_channel,
                slack_level=options.slack_log_level,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ConfigError(f"logging setup failed: {exc}") from exc
        runner_config = _load_or_build_config(args, effective_config)
        runner_config = _apply_cli_overrides(runner_config, args)
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
        logger.debug("Runner config: %s", runner_config)
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
        logging.getLogger("parishkit.runner").critical("%s", exc)
        return exc.exit_code
    except ConfigError as exc:
        logging.getLogger("parishkit.runner").critical("configuration error: %s", exc)
        return EXIT_CONFIG_ERROR


def _load_or_build_config(
    args: argparse.Namespace,
    config_path: Path | None,
) -> RunnerConfig:
    if config_path:
        return load_runner_config(config_path)
    if args.command is not None:
        return _single_command_config(args)
    raise RunnerConfigError(
        f"no runner config found at {DEFAULT_RUNNER_CONFIG}; "
        "provide --config or --command"
    )


def _apply_cli_overrides(
    config: RunnerConfig,
    args: argparse.Namespace,
) -> RunnerConfig:
    lock_path = args.lock_file or config.lock.path
    stale_after = (
        timedelta(seconds=parse_duration(args.stale_after))
        if args.stale_after
        else config.lock.stale_after
    )
    stale_action = args.stale_action or config.lock.stale_action
    timeout = parse_duration(args.timeout) if args.timeout else None
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
    if args.config:
        return Path(args.config).expanduser().resolve()
    if args.command is not None:
        return None
    if DEFAULT_RUNNER_CONFIG.exists():
        return DEFAULT_RUNNER_CONFIG
    return None


def _log_summary(
    logger: logging.Logger,
    config: RunnerConfig,
    exit_code: int,
    results: list[JobResult],
) -> None:
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
    previous_handlers = {
        signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)
    }

    def handle_signal(signum: int, _frame: object) -> None:
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
