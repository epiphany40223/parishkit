import json
import logging
import multiprocessing
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
    lock_path = tmp_path / "runner.lock"

    with LockFile(LockConfig(path=lock_path), command=["test"]):
        assert lock_path.exists()
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["command"] == ["test"]
        assert metadata["pid"]

    assert not lock_path.exists()


def test_active_lock_exits(tmp_path):
    lock_path = tmp_path / "runner.lock"
    lock_path.write_text(
        json.dumps({"start_time": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )

    with pytest.raises(LockUnavailable):
        LockFile(LockConfig(path=lock_path), command=["test"]).acquire()


def test_lock_filesystem_error_raises_lock_unavailable(tmp_path):
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("file", encoding="utf-8")

    with pytest.raises(LockUnavailable, match="runner lock failed"):
        LockFile(
            LockConfig(path=parent_file / "runner.lock"), command=["test"]
        ).acquire()


def test_stale_lock_remove_and_continue(tmp_path):
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
    lock_path = tmp_path / "runner.lock"
    stale_metadata = {
        "token": "stale",
        "start_time": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
    }
    lock_path.write_text(json.dumps(stale_metadata), encoding="utf-8")
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
    assert is_lock_stale({}, timedelta(minutes=1))


def test_run_job_success():
    job = JobConfig(
        name="ok",
        command=[sys.executable, "-c", "print('ok')"],
    )

    result = run_job(job)

    assert result.ok
    assert result.stdout.strip() == "ok"


def test_run_job_timeout():
    job = JobConfig(
        name="slow",
        command=[sys.executable, "-c", "import time; time.sleep(2)"],
        timeout=0.1,
    )

    result = run_job(job)

    assert result.timed_out
    assert result.returncode == EXIT_TIMEOUT


def test_run_job_reports_startup_failure(tmp_path):
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
    jobs = [JobConfig("disabled", ["true"], enabled=False)]

    assert select_jobs(jobs, ["disabled"], include_disabled=False) == []
    assert select_jobs(jobs, ["disabled"], include_disabled=True) == jobs


def test_select_jobs_unknown_fails():
    with pytest.raises(RunnerConfigError, match="unknown job"):
        select_jobs([], ["missing"])


def test_parse_runner_config_resolves_jobs(tmp_path):
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
    with pytest.raises(Exception, match=message):
        parse_runner_config(config)


def test_parse_duration_rejects_bool():
    with pytest.raises(Exception, match="duration"):
        parse_duration(True)


def test_parse_runner_config_rejects_shell_string_command():
    with pytest.raises(Exception, match="command"):
        parse_runner_config({"jobs": [{"name": "bad", "command": "echo ok"}]})


def test_parse_runner_config_rejects_duplicate_job_names():
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
    config_file = tmp_path / "runner.yaml"
    config_file.write_text("jobs: []\n", encoding="utf-8")

    assert main(["--config", str(config_file), "--command", "true"]) == 2


def test_main_rejects_empty_command_even_when_default_config_exists(
    monkeypatch, tmp_path
):
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
    monkeypatch.setattr(
        "parishkit.pk_cron_runner.DEFAULT_RUNNER_CONFIG", Path("/missing.yaml")
    )

    assert main([]) == 2


def test_success_summary_can_notify_via_logger_critical():
    messages = []

    class FakeLogger:
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
    messages = []

    class FakeLogger:
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
