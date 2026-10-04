"""The local campaign seeder's command, waits, signals and drive loop (#476).

No database: the LOCAL refusal before any connection, the argument checks,
the ``timeline`` step's document, the bounded waits with their durable
timeout log, the two scheduler signals, the drive loop's ordering around
jumps (settle before every jump, signal after it, settle after occurrence
instants, late events at once), the configuration patch and the invariant
``DO`` block's shape.
"""

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from parishkit.stewardship.cli import main
from parishkit.stewardship.local import seed_timeline, seeder
from parishkit.stewardship.local.seeder import (
    DriveReport,
    SeedFailed,
    SeedRefused,
    SeedRequest,
    drive_timeline,
    invariant_sql,
    schedule_patch,
    wait_until,
)

NOW = "2026-10-07T15:30:00+00:00"
ARGUMENTS = [
    "--seed",
    "7",
    "--families",
    "100",
    "--anchor-date",
    "2026-09-20",
    "--now",
    NOW,
]


@pytest.fixture
def quiet(monkeypatch):
    """CLI tests must not install the runtime's stderr logging handler."""
    monkeypatch.setattr(
        "parishkit.stewardship.observability.configure_logging", lambda *a, **k: None
    )
    monkeypatch.delenv("PARISHKIT_STEWARDSHIP_PROFILE", raising=False)


def deployment_file(tmp_path, profile):
    """A minimal deployment YAML naming one profile."""
    path = tmp_path / f"{profile}.yaml"
    path.write_text(yaml.safe_dump({"deployment": {"profile": profile}}))
    return str(path)


# The command.
def test_command_refuses_outside_local_before_any_step(
    quiet, monkeypatch, tmp_path, capsys
):
    """Only an admitted LOCAL deployment runs a step; nothing else is touched."""
    run = Mock()
    monkeypatch.setattr(seeder, "run_step", run)
    monkeypatch.setattr(seeder, "timeline_summary", run)
    for profile in ("production", "development", "test"):
        assert (
            main(["local-seed", "--profile", profile, "--step", "timeline", *ARGUMENTS])
            == 2
        )
        monkeypatch.setenv("PARISHKIT_STEWARDSHIP_PROFILE", profile)
        assert main(["local-seed", "--step", "timeline", *ARGUMENTS]) == 2
        monkeypatch.delenv("PARISHKIT_STEWARDSHIP_PROFILE")
    assert main(["local-seed", "--step", "prepare", *ARGUMENTS]) == 2
    development = deployment_file(tmp_path, "development")
    assert (
        main(["local-seed", "--config", development, "--step", "check", *ARGUMENTS])
        == 2
    )
    run.assert_not_called()
    assert str(tmp_path) not in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["--step", "seed", *ARGUMENTS],
        ["--step", "timeline", *ARGUMENTS[2:]],
        ["--step", "timeline", "--seed", "x", *ARGUMENTS[2:]],
        ["--step", "timeline", *ARGUMENTS[:2], "--families", "0", *ARGUMENTS[4:]],
        [
            "--step",
            "timeline",
            *ARGUMENTS[:4],
            "--anchor-date",
            "2026-9-20",
            *ARGUMENTS[6:],
        ],
        ["--step", "timeline", *ARGUMENTS[:6], "--now", "2026-10-07T15:30:00"],
        ["--step", "timeline", *ARGUMENTS, "--response-scale", "0"],
        ["--step", "timeline", *ARGUMENTS, "--response-scale", "many"],
    ],
)
def test_command_refuses_bad_arguments_in_local(quiet, monkeypatch, arguments, capsys):
    run = Mock()
    monkeypatch.setattr(seeder, "run_step", run)
    assert main(["local-seed", "--profile", "local", *arguments]) == 2
    run.assert_not_called()
    assert "ERROR" in capsys.readouterr().err


def test_timeline_step_prints_the_summary_without_a_database(
    quiet, monkeypatch, capsys
):
    """The preview derives the eligible Families from the synthetic parish alone."""
    run = Mock()
    monkeypatch.setattr(seeder, "run_step", run)
    assert (
        main(["local-seed", "--profile", "local", "--step", "timeline", *ARGUMENTS])
        == 0
    )
    run.assert_not_called()
    document = json.loads(capsys.readouterr().out)
    assert document["step"] == "timeline"
    assert document["start"] == "2026-09-26" and document["end"] == "2026-10-26"
    assert document["elapsed_days"] == 11
    assert len(document["reminders"]) == 8
    assert 85 <= document["eligible"] <= 95
    assert sum(document["daily_submissions"]) == 13
    assert document["stages"]["submitted"] >= 13
    assert document["events"]["initial"] == 1 and document["events"]["midnight"] == 11
    assert document["last_event"] <= NOW
    assert "elapsed_seconds" in document
    # The scale passes through and the clock directory defaults to the mount.
    request = SeedRequest.parse(
        SimpleNamespace(
            step="drive",
            seed="7",
            families="100",
            anchor_date="2026-09-20",
            now=NOW,
            response_scale="2.5",
            clock_dir=None,
        )
    )
    assert request.response_scale == 2.5
    assert str(request.clock_directory) == "/run/parishkit-clock"
    assert request.now == datetime(2026, 10, 7, 15, 30, tzinfo=UTC)


# Waits.
def test_wait_until_returns_elapsed_and_logs_the_unmet_condition_on_expiry(caplog):
    """Every timeout logs what was waited for, what was unmet, the limit and elapsed."""
    slept = []
    answers = iter(["still queued", "still queued", True])
    elapsed = wait_until("work", lambda: next(answers), limit=10, sleep=slept.append)
    assert elapsed >= 0 and len(slept) == 2 and all(0 < s <= 1 for s in slept)
    clock = iter([0.0, 0.0, 5.0, 5.0, 9.5, 9.5, 12.0, 12.0, 12.0])
    import parishkit.stewardship.local.seeder as module

    original = module.time.monotonic
    module.time.monotonic = lambda: next(clock)
    try:
        with (
            caplog.at_level(logging.ERROR),
            pytest.raises(SeedFailed, match="Timed out"),
        ):
            wait_until(
                "work to settle",
                lambda: "3 tasks queued",
                limit=10,
                sleep=lambda s: None,
            )
    finally:
        module.time.monotonic = original
    record = caplog.records[-1]
    message = record.getMessage()
    assert "waiting for work to settle" in message
    assert "unmet: 3 tasks queued" in message
    assert "limit 10 s" in message
    assert "elapsed 12.0 s" in message


# The drive loop.
class FakeClock:
    """A forward-only clock whose jumps are recorded, with no real waiting."""

    def __init__(self, start):
        self.current = start
        self.jumps = []

    def jump_to(self, target):
        if target <= self.current:
            return None
        self.jumps.append(target)
        self.current = target
        return int((target - self.current).total_seconds())

    def now(self):
        return self.current


class FakeDriver:
    """Records every Family event it is asked to run."""

    def __init__(self):
        self.ran = []

    def run(self, event):
        self.ran.append(event)


def test_drive_settles_before_jumps_waits_for_evidence_and_runs_near_family_events():
    result = seed_timeline.build(
        7,
        100,
        datetime(2026, 10, 7, 15, 30, tzinfo=UTC),
        range(92),
        ministries=range(3),
    )
    # Start the clock after the first few events so they are already late.
    start = result.events[5].at
    clock = FakeClock(start)
    calls = []
    settle = Mock(side_effect=lambda: calls.append("settle") or 0.5)
    evidence_for = []

    def evidence(event):
        """Record which occurrence the loop asked evidence for; answer at once."""
        evidence_for.append(event)
        return lambda: calls.append("evidence") or True

    driver = FakeDriver()
    logged = []
    report = drive_timeline(
        result,
        clock,
        settle=settle,
        driver=driver,
        evidence=evidence,
        log=lambda *args: logged.append(args[0] % args[1:]),
    )
    assert isinstance(report, DriveReport)
    assert report.events == len(result.events)
    # Replay the rule: an occurrence always jumps unless its instant has
    # passed; a Family event jumps only when more than the threshold ahead.
    expected, current = [], start
    for event in result.events:
        if event.is_occurrence:
            if event.at > current:
                expected.append(event.at)
                current = event.at
        elif event.at - current > seeder.JUMP_THRESHOLD:
            expected.append(event.at)
            current = event.at
    assert clock.jumps == expected
    assert report.jumps == len(expected)
    assert report.late == len(result.events) - len(expected)
    assert report.seeded_now == expected[-1]
    # Every occurrence after the start was jumped to exactly, never run early.
    for event in result.occurrences():
        if event.at > start:
            assert event.at in clock.jumps
    # Evidence is awaited for every occurrence instant, in order.
    assert evidence_for == list(result.occurrences())
    # Family events all reached the driver, in timeline order.
    assert driver.ran == list(result.family_events())
    # One settle before every event, one more after each occurrence, one at the end.
    occurrences = len(result.occurrences())
    assert settle.call_count == len(result.events) + occurrences + 1
    assert calls.count("evidence") == occurrences
    assert len(logged) == len(result.events)
    assert logged[0].startswith("seed event 1/")
    # A Family's form costs two jumps at most: the session and the submission.
    submitted = result.stages["submitted"][3]
    own = [
        e
        for e in result.events
        if e.family == submitted and e.data.get("version", 1) == 1
    ]
    assert [e.kind for e in own][:2] == ["session", "baseline"]
    jumped_kinds = [e.kind for e in own if e.at in set(expected)]
    assert set(jumped_kinds) <= {"session", "submission"}


def test_occurrence_evidence_is_defined_for_every_occurrence_kind():
    """Each occurrence kind has a condition; Family kinds have none."""
    from parishkit.stewardship.local.seed_timeline import Event

    at = datetime(2026, 9, 26, 14, 0, tzinfo=UTC)
    with pytest.raises(SeedRefused, match="No occurrence evidence"):
        seeder.occurrence_evidence(Event(at, "session", 1), "c", "America/New_York")
    # The Initial and Reminder conditions read the campaign's Families when
    # they are made, so they are covered by the database test alone.
    for kind in ("boundary_start", "midnight"):
        assert callable(seeder.occurrence_evidence(Event(at, kind), "c", "UTC"))


# Phase 1 patch and phase 3 SQL.
def test_schedule_patch_applies_dates_the_initial_and_eight_reminders():
    campaign_id = "11111111-1111-1111-1111-111111111111"
    document = {
        "sections": {
            "schedules": [
                {
                    "id": "aaaa",
                    "values": {
                        "campaign_id": campaign_id,
                        "kind": "initial",
                        "date": "2026-10-01",
                        "time": "09:00:00",
                        "weekday": None,
                        "subject": "Invitation",
                        "template_version": "tttt",
                    },
                },
                {
                    "id": "bbbb",
                    "values": {
                        "campaign_id": campaign_id,
                        "kind": "reminder",
                        "date": "2026-10-08",
                        "time": "09:00:00",
                        "weekday": None,
                        "subject": "Reminder",
                        "template_version": "rrrr",
                    },
                },
                {
                    "id": "cccc",
                    "values": {
                        "campaign_id": campaign_id,
                        "kind": "daily_digest",
                        "date": None,
                        "time": "06:00:00",
                        "weekday": None,
                        "subject": "Digest",
                        "template_version": "dddd",
                    },
                },
                {
                    "id": "other",
                    "values": {"campaign_id": "other", "kind": "initial"},
                },
            ],
            "content": [
                {
                    "id": "eeee",
                    "values": {
                        "campaign_id": campaign_id,
                        "kind": "email",
                        "slot": "reminder",
                        "subject": "Reminder email",
                    },
                },
                {
                    "id": "ffff",
                    "values": {
                        "campaign_id": "other",
                        "kind": "email",
                        "slot": "reminder",
                        "subject": "Other",
                    },
                },
            ],
        }
    }
    cal = seed_timeline.calendar(
        datetime(2026, 10, 7, 15, 30, tzinfo=UTC), "America/New_York"
    )
    patch = schedule_patch(document, campaign_id, cal)
    assert patch[0] == {
        "operation": "update",
        "section": "campaigns",
        "id": campaign_id,
        "values": {"start_date": "2026-09-26", "end_date": "2026-10-26"},
    }
    assert patch[1] == {
        "operation": "update",
        "section": "schedules",
        "id": "aaaa",
        "values": {"date": "2026-09-26", "time": "10:00:00"},
    }
    assert patch[2] == {"operation": "remove", "section": "schedules", "id": "bbbb"}
    added = patch[3:]
    assert len(added) == 8
    for row, instant in zip(added, cal.reminders, strict=True):
        assert row["operation"] == "add" and row["section"] == "schedules"
        assert row["values"]["kind"] == "reminder"
        # The campaign's Reminder email revision and its subject, as the
        # content validation requires of a Reminder schedule.
        assert row["values"]["template_version"] == "eeee"
        assert row["values"]["subject"] == "Reminder email"
        assert row["values"]["date"] == instant.date().isoformat()
        assert row["values"]["time"] == "09:00:00"
        assert row["values"]["weekday"] is None
        assert row["values"]["campaign_id"] == campaign_id
    assert len({row["id"] for row in added}) == 8
    # The digest schedule is untouched, and the other campaign's rows ignored.
    assert not any(step.get("id") in {"cccc", "other"} for step in patch)
    # Without a Reminder email revision the existing Reminder schedule's
    # reference is reused, then the Initial's; no Initial is refused.
    del document["sections"]["content"]
    patched = schedule_patch(document, campaign_id, cal)
    assert patched[3]["values"]["template_version"] == "rrrr"
    del document["sections"]["schedules"][1]
    patched = schedule_patch(document, campaign_id, cal)
    assert patched[2]["values"]["template_version"] == "tttt"
    del document["sections"]["schedules"][0]
    with pytest.raises(SeedRefused, match="exactly one Initial"):
        schedule_patch(document, campaign_id, cal)


def test_invariant_sql_is_a_self_verifying_do_block_over_the_real_tables():
    seeded_now = datetime(2026, 10, 7, 15, 25, 12, tzinfo=UTC)
    sql = invariant_sql(seeded_now, {"submission": 14, "midnight": 11})
    assert sql.startswith("DO $$\nDECLARE\n") and sql.rstrip().endswith("END $$;")
    assert "seeded_now timestamptz := '2026-10-07T15:25:12+00:00';" in sql
    columns = sum(len(names) for _, names in seeder._SEEDED_NOW_COLUMNS)
    assert sql.count("RAISE EXCEPTION 'seed invariant:") == 6 + 2 + columns
    assert "IF NOT (n = 14) THEN" in sql and "IF NOT (n >= 11) THEN" in sql
    # With the start date the elapsed days must each have a fact row, exactly.
    from datetime import date

    exact = invariant_sql(seeded_now, {"midnight": 11}, start=date(2026, 9, 26))
    assert "local_date >= '2026-09-26' AND local_date < '2026-10-07'" in exact
    assert "expected exactly 11" in exact
    assert "baselines before their session" in sql
    assert "delivered fulfillments before their message was delivered" in sql
    assert "stewardship_family_engagement" in sql
    for table in (
        "stewardship_schedule_fulfillment",
        "stewardship_schedule_occurrence",
        "stewardship_family_form_baseline",
        "stewardship_submission",
        "stewardship_submission_receipt",
        "stewardship_family_session",
        "stewardship_outbox_message",
        "stewardship_daily_fact",
    ):
        assert table in sql
    # Future-by-design columns are never compared against the seeded now.
    for column in ("expires_at", "not_before", "provider_deadline", "ends_at"):
        assert f"{column} > seeded_now" not in sql
    with pytest.raises(SeedRefused, match="start date"):
        invariant_sql(seeded_now, {}, start="2026-09-26")
    assert "due_at > seeded_now" not in sql
    assert "RAISE NOTICE 'seed invariants hold at %'" in sql
    with pytest.raises(SeedRefused, match="seeded now"):
        invariant_sql(datetime(2026, 10, 7), {})


def test_constants_match_the_specification():
    assert seeder.DEFAULT_WAIT_SECONDS == 600
    assert seeder.STEPS == ("timeline", "prepare", "drive", "check", "finish")
    assert sorted(seeder.FATAL_OCCURRENCE_STATES) == [
        "coalesced",
        "delivery_unknown",
        "failed",
    ]
    assert sorted(seeder.FATAL_OUTBOX_STATES) == [
        "delivery_unknown",
        "permanent_failure",
    ]
    assert sorted(seeder.FATAL_TASK_STATES) == ["abandoned", "failed"]
    assert sorted(seeder.TERMINAL_OCCURRENCE_STATES) == ["skipped", "succeeded"]
    assert timedelta(seconds=150) == seeder.JUMP_THRESHOLD


def test_seeder_admission_allows_only_the_writable_control_mount():
    """Web's policy applies to every mount but the clock-control one."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.deployment import DeploymentProfile, ServiceRole
    from parishkit.stewardship.local.clock import (
        CLOCK_CONTROL_TARGET,
        CLOCK_MOUNT_TARGET,
    )
    from parishkit.stewardship.service_boundaries import Mount

    from .test_service_boundaries import configured

    config, mounts = configured(ServiceRole.WEB)
    config = replace(config, profile=DeploymentProfile.LOCAL)
    control = Mount(CLOCK_CONTROL_TARGET, False)
    seeder.admit_seeder_mounts(
        config, [*mounts, control, Mount(CLOCK_MOUNT_TARGET, True)]
    )
    with pytest.raises(SeedRefused, match="writable at its control path"):
        seeder.admit_seeder_mounts(config, mounts)
    with pytest.raises(SeedRefused, match="writable at its control path"):
        seeder.admit_seeder_mounts(config, [*mounts, Mount(CLOCK_CONTROL_TARGET, True)])
    # Any other extra mount still goes through web's policy and is refused,
    # as is a writable clock at the services' own target.
    with pytest.raises(ConfigError, match="unrecognized mount"):
        seeder.admit_seeder_mounts(
            config, [*mounts, control, Mount(Path("/srv/x"), True)]
        )
    with pytest.raises(ConfigError, match="unrecognized mount"):
        seeder.admit_seeder_mounts(
            config, [*mounts, control, Mount(CLOCK_MOUNT_TARGET, False)]
        )


def test_seeder_admission_refuses_root_and_other_profiles(monkeypatch):
    from parishkit.stewardship.deployment import DeploymentProfile, ServiceRole
    from parishkit.stewardship.local.clock import CLOCK_CONTROL_TARGET
    from parishkit.stewardship.service_boundaries import Mount

    from .test_service_boundaries import configured

    config, mounts = configured(ServiceRole.WEB)
    mounts = [*mounts, Mount(CLOCK_CONTROL_TARGET, False)]
    with pytest.raises(SeedRefused, match="only in the local profile"):
        seeder.admit_seeder_mounts(
            replace(config, profile=DeploymentProfile.PRODUCTION), mounts
        )
    monkeypatch.setattr(seeder.os, "geteuid", lambda: 0)
    with pytest.raises(SeedRefused, match="as root"):
        seeder.admit_seeder_mounts(
            replace(config, profile=DeploymentProfile.LOCAL), mounts
        )
