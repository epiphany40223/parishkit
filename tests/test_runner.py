import json
import logging
import multiprocessing
import stat
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parishkit import pk_cron_runner as runner
from parishkit.pk_cron_runner import (
    EXIT_JOB_FAILED,
    EXIT_SUCCESS,
    EXIT_TIMEOUT,
    JobConfig,
    LockConfig,
    LockFile,
    LockUnavailable,
    RunnerConfig,
    RunnerConfigError,
    _failure_summary,
    is_lock_stale,
    main,
    parse_duration,
    parse_runner_config,
    run_job,
    run_jobs,
    select_jobs,
)


def _acquire_stale_lock_for_test(lock_path: str, queue: multiprocessing.Queue) -> None:
    """Child-process target: try to take over a stale lock and report outcome.

    Runs in a separate spawned process so two of them race over the same stale
    lock. Reports ``"acquired"`` on success (then briefly holds the lock) or
    ``"locked"`` if another process won the takeover.
    """
    try:
        lock_config = LockConfig(
            path=Path(lock_path),
            stale_after=timedelta(minutes=1),
            stale_action="remove-and-continue",
        )
        with LockFile(lock_config, command=["test"]):
            queue.put("acquired")
            time.sleep(0.3)
    except LockUnavailable:
        queue.put("locked")


def test_lock_acquisition_writes_metadata_and_cleans_up(tmp_path):
    """Acquiring a lock writes its metadata file; exiting the context removes
    it."""
    lock_path = tmp_path / "runner.lock"

    with LockFile(LockConfig(path=lock_path), command=["test"]):
        assert lock_path.exists()
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["command"] == ["test"]
        assert metadata["pid"]

    assert not lock_path.exists()


def test_active_lock_exits(tmp_path):
    """A fresh (non-stale) existing lock blocks acquisition with
    LockUnavailable."""
    lock_path = tmp_path / "runner.lock"
    lock_path.write_text(
        json.dumps({"start_time": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )

    with pytest.raises(LockUnavailable):
        LockFile(LockConfig(path=lock_path), command=["test"]).acquire()


def test_lock_filesystem_error_raises_lock_unavailable(tmp_path):
    """A filesystem error while creating the lock is wrapped as
    LockUnavailable."""
    # Use a regular file as the lock's parent directory so the create fails.
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("file", encoding="utf-8")

    with pytest.raises(LockUnavailable, match="runner lock failed"):
        LockFile(
            LockConfig(path=parent_file / "runner.lock"), command=["test"]
        ).acquire()


def test_stale_lock_remove_and_continue(tmp_path):
    """A stale lock with remove-and-continue is taken over, then removed on
    release."""
    lock_path = tmp_path / "runner.lock"
    lock_path.write_text(
        json.dumps(
            {"start_time": (datetime.now(UTC) - timedelta(hours=2)).isoformat()}
        ),
        encoding="utf-8",
    )

    lock = LockFile(
        LockConfig(
            path=lock_path,
            stale_after=timedelta(minutes=1),
            stale_action="remove-and-continue",
        ),
        command=["test"],
    )
    lock.acquire()
    lock.release()

    assert not lock_path.exists()


def test_stale_lock_remove_and_continue_detects_replaced_lock(monkeypatch, tmp_path):
    """Takeover aborts if the lock's metadata changes between the staleness
    check and the removal, signalling another runner replaced it."""
    lock_path = tmp_path / "runner.lock"
    stale_metadata = {
        "token": "stale",
        "start_time": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
    }
    replacement_metadata = {
        "token": "replacement",
        "start_time": datetime.now(UTC).isoformat(),
    }
    lock_path.write_text(json.dumps(stale_metadata), encoding="utf-8")
    # First read sees the stale lock; the re-read sees a different token,
    # simulating a concurrent replacement of the lock during takeover.
    reads = iter([stale_metadata, replacement_metadata])
    monkeypatch.setattr(runner, "read_lock_metadata", lambda _path: next(reads))

    lock = LockFile(
        LockConfig(
            path=lock_path,
            stale_after=timedelta(minutes=1),
            stale_action="remove-and-continue",
        ),
        command=["test"],
    )

    with pytest.raises(LockUnavailable, match="changed"):
        lock.acquire()

    assert lock_path.exists()


def test_stale_lock_remove_and_continue_rejects_unreadable_reread(
    monkeypatch, tmp_path
):
    """Takeover aborts if the lock becomes unreadable (empty metadata) on the
    re-read, rather than risking removal of a lock it can no longer verify."""
    lock_path = tmp_path / "runner.lock"
    stale_metadata = {
        "token": "stale",
        "start_time": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
    }
    lock_path.write_text(json.dumps(stale_metadata), encoding="utf-8")
    # Re-read returns empty metadata, modelling a lock that vanished or was
    # rewritten and can no longer be confirmed as the same stale lock.
    reads = iter([stale_metadata, {}])
    monkeypatch.setattr(runner, "read_lock_metadata", lambda _path: next(reads))

    lock = LockFile(
        LockConfig(
            path=lock_path,
            stale_after=timedelta(minutes=1),
            stale_action="remove-and-continue",
        ),
        command=["test"],
    )

    with pytest.raises(LockUnavailable, match="changed"):
        lock.acquire()

    assert lock_path.exists()


def test_stale_lock_remove_and_continue_rejects_empty_metadata(tmp_path):
    """A lock file with empty/unparseable metadata is not treated as stale and
    removed; acquisition fails and the file is left in place."""
    lock_path = tmp_path / "runner.lock"
    lock_path.write_text("", encoding="utf-8")
    lock = LockFile(
        LockConfig(
            path=lock_path,
            stale_after=timedelta(minutes=1),
            stale_action="remove-and-continue",
        ),
        command=["test"],
    )

    with pytest.raises(LockUnavailable, match="metadata"):
        lock.acquire()

    assert lock_path.exists()


def test_stale_lock_recovery_serializes_concurrent_runners(tmp_path):
    """When two real processes race to take over the same stale lock, exactly
    one acquires it and the other is blocked, with both exiting cleanly."""
    lock_path = tmp_path / "runner.lock"
    lock_path.write_text(
        json.dumps(
            {
                "token": "stale",
                "start_time": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    # Use "spawn" for a clean, fork-state-free child on every platform.
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_acquire_stale_lock_for_test,
            args=(str(lock_path), queue),
        )
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    outcomes = [queue.get(timeout=5) for _ in processes]
    for process in processes:
        process.join(timeout=5)

    assert sorted(outcomes) == ["acquired", "locked"]
    assert all(process.exitcode == 0 for process in processes)


def test_lock_release_does_not_remove_replaced_lock(tmp_path):
    """Releasing a stale lock that has already been replaced must not delete the
    replacement: the on-disk lock still belongs to the new owner."""
    lock_path = tmp_path / "runner.lock"
    stale_lock = LockFile(
        LockConfig(path=lock_path),
        command=["stale"],
    )
    stale_lock.acquire()
    replacement_lock = LockFile(
        LockConfig(
            path=lock_path,
            stale_after=timedelta(seconds=0),
            stale_action="remove-and-continue",
        ),
        command=["replacement"],
    )
    replacement_lock.acquire()

    stale_lock.release()

    metadata = json.loads(lock_path.read_text(encoding="utf-8"))
    assert metadata["command"] == ["replacement"]
    replacement_lock.release()


def test_lock_release_logs_unlink_failure(monkeypatch, tmp_path, caplog):
    """If removing the lock file fails on release, the error is logged as a
    warning and the lock is still marked released."""
    lock_path = tmp_path / "runner.lock"
    lock = LockFile(LockConfig(path=lock_path), command=["test"])
    lock.acquire()

    def fail_unlink(_path):
        raise OSError("read-only")

    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with caplog.at_level(logging.WARNING, logger="parishkit.pk_cron_runner"):
        lock.release()

    assert "failed to remove runner lock" in caplog.text
    assert not lock._acquired  # noqa: SLF001 - focused cleanup-state regression


def test_is_lock_stale_with_bad_metadata():
    """Metadata with no usable start time is treated as stale."""
    assert is_lock_stale({}, timedelta(minutes=1))


def test_run_job_success():
    """A job that exits zero is reported ok with its captured stdout."""
    job = JobConfig(
        name="ok",
        command=[sys.executable, "-c", "print('ok')"],
    )

    result = run_job(job)

    assert result.ok
    assert result.stdout.strip() == "ok"


def test_run_job_bounds_captured_output(monkeypatch):
    """Large child output is truncated before it is returned in JobResult."""
    monkeypatch.setattr("parishkit.pk_cron_runner.MAX_CAPTURED_OUTPUT_BYTES", 12)
    job = JobConfig(
        name="chatty",
        command=[sys.executable, "-c", "print('0123456789abcdef')"],
    )

    result = run_job(job)

    assert result.ok
    assert result.stdout.startswith("... output truncated ...")
    assert result.stdout.endswith("56789abcdef\n")


def test_redacted_runner_config_hides_likely_secret_values():
    """Debug serialization redacts env values and CLI secret arguments."""
    config = RunnerConfig(
        jobs=[
            JobConfig(
                "secret",
                [
                    "tool",
                    "--token",
                    "abc123",
                    "--client-secret=def456",
                    "--plain",
                    "visible",
                ],
                env={"API_TOKEN": "secret", "VISIBLE": "ok"},
            )
        ]
    )

    redacted = runner.redacted_runner_config(config)

    assert redacted["jobs"][0]["env"] == {
        "API_TOKEN": "[redacted]",
        "VISIBLE": "ok",
    }
    assert redacted["jobs"][0]["command"] == [
        "tool",
        "--token",
        "[redacted]",
        "--client-secret=[redacted]",
        "--plain",
        "visible",
    ]


def test_lock_metadata_redacts_likely_secret_command_values(tmp_path):
    """Lock files do not expose likely secret command arguments."""
    lock_path = tmp_path / "runner.lock"

    with LockFile(
        LockConfig(path=lock_path),
        command=["tool", "--token", "secret", "--plain", "visible"],
    ):
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))

    assert metadata["command"] == [
        "tool",
        "--token",
        "[redacted]",
        "--plain",
        "visible",
    ]


def test_run_jobs_logs_redacted_command_arguments(caplog):
    """Job start logs redact likely secret command arguments."""
    config = RunnerConfig(
        jobs=[
            JobConfig(
                "redaction",
                [sys.executable, "-c", "raise SystemExit(0)", "--token", "secret"],
            )
        ]
    )
    logger = logging.getLogger("test.runner.redaction")

    with caplog.at_level(logging.INFO, logger=logger.name):
        exit_code, _results = run_jobs(config, logger=logger)

    assert exit_code == EXIT_SUCCESS
    assert "--token '[redacted]'" in caplog.text
    assert "secret" not in caplog.text


def test_run_job_timeout():
    """A job that exceeds its timeout is flagged timed_out with the timeout
    exit code."""
    job = JobConfig(
        name="slow",
        command=[sys.executable, "-c", "import time; time.sleep(2)"],
        timeout=0.1,
    )

    result = run_job(job)

    assert result.timed_out
    assert result.returncode == EXIT_TIMEOUT


def test_run_job_reports_startup_failure(tmp_path):
    """A job that cannot even start (missing cwd) is reported as a failure with
    the job-failed exit code and the OS error in stderr."""
    job = JobConfig(
        name="bad-cwd",
        command=[sys.executable, "-c", "print('never')"],
        cwd=tmp_path / "missing",
    )

    result = run_job(job)

    assert not result.ok
    assert result.returncode == EXIT_JOB_FAILED
    assert "No such file" in result.stderr or "cannot find" in result.stderr


def test_run_jobs_continue_and_summarize():
    """With stop_on_first_failure off, a failing job does not halt the run; all
    jobs run and the overall exit code reflects the failure."""
    config = RunnerConfig(
        jobs=[
            JobConfig("bad", [sys.executable, "-c", "raise SystemExit(7)"]),
            JobConfig("ok", [sys.executable, "-c", "print('ok')"]),
        ],
        stop_on_first_failure=False,
    )

    exit_code, results = run_jobs(config, logger=logging.getLogger("test.runner"))

    assert exit_code == EXIT_JOB_FAILED
    assert [result.name for result in results] == ["bad", "ok"]


def test_run_jobs_captures_output_without_logging_it(caplog):
    """Child stdout/stderr stay in results but are not copied into runner logs."""
    config = RunnerConfig(
        jobs=[
            JobConfig(
                "chatty",
                [
                    sys.executable,
                    "-c",
                    (
                        "import os, sys; "
                        "print(os.environ['STDOUT_TEXT']); "
                        "print(os.environ['STDERR_TEXT'], file=sys.stderr)"
                    ),
                ],
                env={
                    "STDOUT_TEXT": "child stdout",
                    "STDERR_TEXT": "child stderr",
                },
            )
        ],
    )
    logger = logging.getLogger("test.runner.output")

    with caplog.at_level(logging.INFO, logger=logger.name):
        exit_code, results = run_jobs(config, logger=logger)

    assert exit_code == EXIT_SUCCESS
    assert results[0].stdout.strip() == "child stdout"
    assert results[0].stderr.strip() == "child stderr"
    assert "running job chatty" in caplog.text
    assert "child stdout" not in caplog.text
    assert "child stderr" not in caplog.text


def test_run_jobs_preserves_timeout_exit_code_when_continuing():
    """When continuing past failures, an earlier timeout's exit code wins over a
    later ordinary failure in the aggregate result."""
    config = RunnerConfig(
        jobs=[
            JobConfig(
                "slow",
                [sys.executable, "-c", "import time; time.sleep(2)"],
                timeout=0.1,
            ),
            JobConfig("bad", [sys.executable, "-c", "raise SystemExit(7)"]),
        ],
        stop_on_first_failure=False,
    )

    exit_code, results = run_jobs(config, logger=logging.getLogger("test.runner"))

    assert exit_code == EXIT_TIMEOUT
    assert [result.name for result in results] == ["slow", "bad"]


def test_select_jobs_skips_disabled_explicitly_by_default():
    """A disabled job is skipped by default but included when
    include_disabled is set, even when named explicitly."""
    jobs = [JobConfig("disabled", ["true"], enabled=False)]

    assert select_jobs(jobs, ["disabled"], include_disabled=False) == []
    assert select_jobs(jobs, ["disabled"], include_disabled=True) == jobs


def test_select_jobs_unknown_fails():
    """Selecting a job name that does not exist raises a config error."""
    with pytest.raises(RunnerConfigError, match="unknown job"):
        select_jobs([], ["missing"])


def test_parse_runner_config_resolves_jobs(tmp_path):
    """Parsing resolves relative paths against base_dir, parses durations into
    seconds, and carries through runner-level settings."""
    config = parse_runner_config(
        {
            "lock": {
                "path": "runner.lock",
                "stale_after": "1m",
                "stale_action": "remove-and-continue",
            },
            "runner": {"stop_on_first_failure": False},
            "jobs": [
                {
                    "name": "job",
                    "command": ["echo", "ok"],
                    "cwd": ".",
                    "env": {"A": "B"},
                    "timeout": "2s",
                }
            ],
        },
        base_dir=tmp_path,
    )

    assert config.lock.path == tmp_path / "runner.lock"
    assert config.jobs[0].cwd == tmp_path
    assert config.jobs[0].timeout == 2
    assert not config.stop_on_first_failure


def test_parse_runner_config_rejects_string_booleans():
    """A string in place of a real boolean for ``enabled`` is rejected rather
    than silently coerced."""
    with pytest.raises(Exception, match="enabled"):
        parse_runner_config(
            {"jobs": [{"name": "bad", "command": ["echo", "ok"], "enabled": "false"}]}
        )


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"lock": {"stale_action": ["remove-and-continue"]}}, "stale_action"),
        ({"runner": {"context": {"bad": "value"}}}, "runner.context"),
        ({"slack": {"context": ["bad"]}}, "slack.context"),
    ],
)
def test_parse_runner_config_rejects_malformed_scalar_values(config, message):
    """Fields expecting a scalar reject non-scalar values, naming the offending
    field in the error message."""
    with pytest.raises(Exception, match=message):
        parse_runner_config(config)


def test_parse_duration_rejects_bool():
    """A boolean is not accepted as a duration (bool is a subclass of int)."""
    with pytest.raises(Exception, match="duration"):
        parse_duration(True)


def test_parse_runner_config_rejects_shell_string_command():
    """A command must be an argument list, not a shell string, so it is
    rejected."""
    with pytest.raises(Exception, match="command"):
        parse_runner_config({"jobs": [{"name": "bad", "command": "echo ok"}]})


def test_parse_runner_config_rejects_unknown_job_keys():
    """Misspelled job keys fail instead of silently changing scheduling."""
    with pytest.raises(Exception, match="job has unsupported key"):
        parse_runner_config(
            {"jobs": [{"name": "bad", "command": ["true"], "timeot": "1m"}]}
        )


def test_parse_runner_config_rejects_duplicate_job_names():
    """Two jobs sharing a name are rejected, since names must be unique
    selectors."""
    with pytest.raises(Exception, match="duplicate job name"):
        parse_runner_config(
            {
                "jobs": [
                    {"name": "same", "command": ["echo", "one"]},
                    {"name": "same", "command": ["echo", "two"]},
                ]
            }
        )


def test_main_runs_single_cli_command_without_config(tmp_path):
    """--command runs an ad hoc command with no config file and succeeds."""
    exit_code = main(
        [
            "--lock-file",
            str(tmp_path / "manual.lock"),
            "--command",
            sys.executable,
            "-c",
            "print('ok')",
        ]
    )

    assert exit_code == EXIT_SUCCESS


def test_main_command_mode_ignores_default_config(monkeypatch, tmp_path):
    """In --command mode the default config is ignored: the ad hoc command runs
    (writing the marker) instead of the configured job that would exit 99.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    config_file = tmp_path / "runner.yaml"
    marker = tmp_path / "marker.txt"
    config_file.write_text(
        f"""
lock:
  path: {tmp_path / "default.lock"}
jobs:
  - name: default
    command:
      - {sys.executable}
      - -c
      - "raise SystemExit(99)"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("parishkit.pk_cron_runner.DEFAULT_RUNNER_CONFIG", config_file)

    exit_code = main(
        [
            "--lock-file",
            str(tmp_path / "manual.lock"),
            "--command",
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).write_text('command')",
        ]
    )

    assert exit_code == EXIT_SUCCESS
    assert marker.read_text(encoding="utf-8") == "command"


def test_main_rejects_command_with_explicit_config(tmp_path):
    """Combining --command with an explicit --config is a usage error
    (exit 2)."""
    config_file = tmp_path / "runner.yaml"
    config_file.write_text("jobs: []\n", encoding="utf-8")

    assert main(["--config", str(config_file), "--command", "true"]) == 2


def test_main_rejects_empty_command_even_when_default_config_exists(
    monkeypatch, tmp_path
):
    """An empty --command is a usage error even when a default config exists;
    the default job must not run as a fallback."""
    config_file = tmp_path / "runner.yaml"
    marker = tmp_path / "marker.txt"
    config_file.write_text(
        f"""
lock:
  path: {tmp_path / "runner.lock"}
jobs:
  - name: default
    command:
      - {sys.executable}
      - -c
      - "from pathlib import Path; Path({str(marker)!r}).write_text('default')"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("parishkit.pk_cron_runner.DEFAULT_RUNNER_CONFIG", config_file)

    assert main(["--command"]) == 2
    assert not marker.exists()


def test_main_returns_config_error_for_logging_setup_failure(tmp_path):
    """A logging setup failure (Slack channel with no usable config) returns the
    config-error exit code instead of running the command."""
    assert (
        main(
            [
                "--lock-file",
                str(tmp_path / "manual.lock"),
                "--slack-channel",
                "#bot-errors",
                "--command",
                sys.executable,
                "-c",
                "print('never')",
            ]
        )
        == 2
    )


def test_main_applies_lock_and_timeout_overrides_to_configured_run(tmp_path):
    """CLI lock/stale/timeout flags override the YAML config: the override lock
    is used (configured one untouched) and the short timeout fires.

    The setup stays local to this test so fixtures remain easy to understand
    and change.
    """
    config_file = tmp_path / "runner.yaml"
    configured_lock = tmp_path / "configured.lock"
    override_lock = tmp_path / "override.lock"
    config_file.write_text(
        f"""
lock:
  path: {configured_lock}
jobs:
  - name: slow
    command:
      - {sys.executable}
      - -c
      - "import time; time.sleep(2)"
""",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--config",
            str(config_file),
            "--lock-file",
            str(override_lock),
            "--stale-after",
            "1s",
            "--stale-action",
            "fail-closed",
            "--timeout",
            "1s",
        ]
    )

    assert exit_code == EXIT_TIMEOUT
    assert not configured_lock.exists()
    assert not override_lock.exists()


def test_main_runs_selected_yaml_job(tmp_path):
    """Naming a job on the command line runs only that job; the unselected job
    (which would exit 99) is skipped."""
    config_file = tmp_path / "runner.yaml"
    marker = tmp_path / "marker.txt"
    config_file.write_text(
        f"""
lock:
  path: {tmp_path / "runner.lock"}
jobs:
  - name: selected
    command:
      - {sys.executable}
      - -c
      - "from pathlib import Path; Path({str(marker)!r}).write_text('selected')"
  - name: skipped
    command:
      - {sys.executable}
      - -c
      - "raise SystemExit(99)"
""",
        encoding="utf-8",
    )

    exit_code = main(["--config", str(config_file), "selected"])

    assert exit_code == EXIT_SUCCESS
    assert marker.read_text(encoding="utf-8") == "selected"


def test_main_uses_default_config_for_logging(monkeypatch, tmp_path):
    """With no args, the default config is loaded and its logging settings take
    effect, creating the configured log file."""
    config_file = tmp_path / "runner.yaml"
    log_file = tmp_path / "runner.log"
    config_file.write_text(
        f"""
logging:
  log_file: {log_file}
lock:
  path: {tmp_path / "runner.lock"}
jobs:
  - name: selected
    command:
      - {sys.executable}
      - -c
      - "print('default config')"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("parishkit.pk_cron_runner.DEFAULT_RUNNER_CONFIG", config_file)

    assert main([]) == EXIT_SUCCESS
    assert log_file.exists()


def test_main_returns_config_error_without_config_or_command(monkeypatch):
    """With neither a command nor any readable default config, main returns the
    config-error exit code."""
    monkeypatch.setattr(
        "parishkit.pk_cron_runner.DEFAULT_RUNNER_CONFIG", Path("/missing.yaml")
    )

    assert main([]) == 2


def test_success_summary_can_notify_via_logger_critical():
    """With notify_success enabled, a successful run is logged at critical level
    so the notification path (e.g. Slack) is triggered."""
    messages = []

    class FakeLogger:
        """Logger stub that records (level, formatted-message) tuples."""

        def info(self, message, *args):
            messages.append(("info", message % args if args else message))

        def critical(self, message, *args):
            messages.append(("critical", message % args if args else message))

    runner._log_summary(  # noqa: SLF001 - focused behavior test
        FakeLogger(),
        RunnerConfig(notify_success=True, context="Test context"),
        EXIT_SUCCESS,
        [],
    )

    assert messages == [
        ("critical", "Test context: runner completed successfully (0 job(s))")
    ]


def test_failure_summary_reports_critical():
    """A failed run logs a critical summary listing each failed job and its exit
    code."""
    messages = []

    class FakeLogger:
        """Logger stub that records (level, formatted-message) tuples."""

        def info(self, message, *args):
            messages.append(("info", message % args if args else message))

        def critical(self, message, *args):
            messages.append(("critical", message % args if args else message))

    runner._log_summary(  # noqa: SLF001 - focused behavior test
        FakeLogger(),
        RunnerConfig(context="Test context"),
        EXIT_JOB_FAILED,
        [runner.JobResult("bad", 1, "", "")],
    )

    assert messages == [
        ("critical", "Test context: runner failed (1 job(s), exit 1)\n- bad: exit 1")
    ]


def test_failure_summary_includes_failed_job_output_when_configured():
    """When include_output_in_slack is set, the failure summary embeds each
    failed job's stdout and stderr alongside its exit code."""
    message = _failure_summary(
        EXIT_JOB_FAILED,
        [
            runner.JobResult(
                name="bad",
                returncode=7,
                stdout="useful stdout",
                stderr="useful stderr",
            )
        ],
        RunnerConfig(include_output_in_slack=True),
    )

    assert "- bad: exit 7" in message
    assert "useful stderr" in message
    assert "useful stdout" in message


def test_failure_summary_redacts_failed_job_output_for_slack():
    """Slack failure output redacts obvious secrets and email addresses."""
    message = _failure_summary(
        EXIT_JOB_FAILED,
        [
            runner.JobResult(
                name="bad",
                returncode=7,
                stdout=(
                    "contact admin@example.org token=abcdefghijklmnopqrstuvwxyz123456"
                ),
                stderr="API_KEY: secret-value",
            )
        ],
        RunnerConfig(include_output_in_slack=True),
    )

    assert "[redacted-email]" in message
    assert "token=[redacted]" in message
    assert "API_KEY=[redacted]" in message
    assert "admin@example.org" not in message
    assert "secret-value" not in message
