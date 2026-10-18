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
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from parishkit.stewardship.cli import main
from parishkit.stewardship.local import seed_timeline, seeder
from parishkit.stewardship.local import seed_web as seed_web_module
from parishkit.stewardship.local.seeder import (
    DriveReport,
    SeedFailed,
    SeedRefused,
    SeedRequest,
    drive_timeline,
    invariant_sql,
    leader_to_import,
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
    assert main(["local-seed", "--step", "testing", *ARGUMENTS]) == 2
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


def completion_module(monkeypatch, complete):
    """Stand in for setup_completion, which needs a configured Django."""
    monkeypatch.setitem(
        sys.modules,
        "parishkit.stewardship.accounts.setup_completion",
        SimpleNamespace(setup_is_complete=lambda: complete),
    )


def test_credentials_step_reports_the_frozen_attempts_request_ids(monkeypatch):
    """finish-setup's lookup (#1355): the ids, not frozen yet, or already done."""
    monkeypatch.setattr(seeder, "configure_web_process", lambda configuration: None)
    requests = {"parishsoft": "p-id", "google_workspace": "g-id"}
    monkeypatch.setattr(seeder, "frozen_credential_requests", lambda: requests)
    request = SimpleNamespace(step="credentials")
    completion_module(monkeypatch, False)
    assert seeder.run_step(request, None) == {
        "step": "credentials",
        "result": "frozen",
        "requests": requests,
    }
    monkeypatch.setattr(seeder, "frozen_credential_requests", lambda: None)
    assert seeder.run_step(request, None)["result"] == "not frozen"
    # Complete setup is never looked up again.
    monkeypatch.setattr(
        seeder, "frozen_credential_requests", Mock(side_effect=AssertionError)
    )
    completion_module(monkeypatch, True)
    assert seeder.run_step(request, None)["result"] == "setup was already complete"


def test_credentials_step_needs_no_admin_email(quiet, monkeypatch, capsys):
    """The step runs through the command in LOCAL and prints its JSON answer."""
    monkeypatch.setattr(
        seeder,
        "credentials_step",
        lambda request, configuration: {"step": request.step, "result": "not frozen"},
    )
    assert (
        main(["local-seed", "--profile", "local", "--step", "credentials", *ARGUMENTS])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["result"] == "not frozen"


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
    # reference is reused, then the Initial's; with neither it is refused.
    content = document["sections"].pop("content")
    patched = schedule_patch(document, campaign_id, cal)
    assert patched[3]["values"]["template_version"] == "rrrr"
    del document["sections"]["schedules"][1]
    patched = schedule_patch(document, campaign_id, cal)
    assert patched[2]["values"]["template_version"] == "tttt"
    initial = document["sections"]["schedules"].pop(0)
    with pytest.raises(SeedRefused, match="no Reminder email"):
        schedule_patch(document, campaign_id, cal)
    # Create the campaign leaves no schedules (#142): the Initial is added
    # with the campaign's Initial email, the Reminders with its Reminder.
    document["sections"]["content"] = [
        *content,
        {
            "id": "iiii",
            "values": {
                "campaign_id": campaign_id,
                "kind": "email",
                "slot": "initial",
                "subject": "Initial email",
            },
        },
    ]
    patched = schedule_patch(document, campaign_id, cal)
    assert patched[1]["operation"] == "add"
    assert patched[1]["values"] | {"campaign_id": campaign_id} == {
        "campaign_id": campaign_id,
        "kind": "initial",
        "date": "2026-09-26",
        "time": "10:00:00",
        "weekday": None,
        "subject": "Initial email",
        "template_version": "iiii",
    }
    assert [row["values"]["template_version"] for row in patched[2:]] == ["eeee"] * 8
    # No Initial email to send is refused, and so are two Initials.
    document["sections"]["content"] = content
    with pytest.raises(SeedRefused, match="no Initial email"):
        schedule_patch(document, campaign_id, cal)
    document["sections"]["schedules"] += [initial, initial]
    with pytest.raises(SeedRefused, match="more than one Initial"):
        schedule_patch(document, campaign_id, cal)


def test_invariant_sql_is_a_self_verifying_do_block_over_the_real_tables():
    seeded_now = datetime(2026, 10, 7, 15, 25, 12, tzinfo=UTC)
    sql = invariant_sql(seeded_now, {"submission": 14, "midnight": 11})
    assert sql.startswith("DO $$\nDECLARE\n") and sql.rstrip().endswith("END $$;")
    assert "seeded_now timestamptz := '2026-10-07T15:25:12+00:00';" in sql
    columns = sum(len(names) for _, names in seeder._SEEDED_NOW_COLUMNS)
    assert sql.count("RAISE EXCEPTION 'seed invariant:") == 6 + 2 + 3 + columns
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


def test_invariant_sql_pins_the_counts_the_timeline_states():
    """Receipts, baselines and engagement rows are pinned exactly (#499)."""
    seeded_now = datetime(2026, 10, 7, 15, 25, 12, tzinfo=UTC)
    counts = {"submission": 14, "baseline": 21, "engaged": 30, "midnight": 11}
    sql = invariant_sql(seeded_now, counts)
    blocks = sql.split("END IF;")
    receipts, baselines, engaged = (
        next(block for block in blocks if message in block)
        for message in (
            "receipts for live submissions",
            "live form baselines not replaced",
            "live Family engagement rows",
        )
    )
    assert "IF NOT (n = 14)" in receipts and "s.mode = 'live'" in receipts
    assert "IF NOT (n = 21)" in baselines and "state <> 'replaced'" in baselines
    assert "IF NOT (n = 30)" in engaged
    # A key the counts lack expects no rows, so an empty database passes.
    assert "IF NOT (n = 0) THEN\n        RAISE EXCEPTION 'seed invariant: % live " in (
        invariant_sql(seeded_now, {})
    )
    # Outbox and occurrence counts depend on eligibility, so the timeline
    # never pins them: without a campaign there is no mail check (#731).
    assert "mailed AS" not in sql and "not mailed exactly once" not in sql


def test_invariant_sql_derives_who_got_each_initial_and_reminder():
    """With the campaign, four mail checks derive the Families from the rows."""
    seeded_now = datetime(2026, 10, 7, 15, 25, 12, tzinfo=UTC)
    campaign = "0b9e8d4c-2f5a-4d7e-9a1b-3c6d8e0f1a2b"
    sql = invariant_sql(seeded_now, {}, campaign_id=campaign)
    blocks = sql.split("END IF;")
    for message in (
        "an eligible Family that had not responded was not mailed exactly once",
        "emails to a Family that had responded or was not eligible",
        "emails not delivered after the seed settled",
        "Production Initial or Reminder emails without their occurrence",
    ):
        (block,) = [block for block in blocks if message in block]
        assert "IF NOT (n = 0)" in block and campaign in block
    # The expected set is the planning rule, read from the rows.
    assert "f.first_eligible_at <= i.due_at" in sql
    assert "s.created_at < i.due_at" in sql
    assert "f.active AND f.portal_eligible AND f.email_eligible" in sql
    # Only a real UUID can name the campaign in the SQL.
    with pytest.raises(ValueError):
        invariant_sql(seeded_now, {}, campaign_id="x'; DROP TABLE y; --")


def test_expected_counts_add_the_families_the_timeline_drives():
    """Every Family with an event signs in, so each has one engagement row."""
    result = seed_timeline.build(
        7, 100, datetime(2026, 10, 7, 15, 30, tzinfo=UTC), range(92)
    )
    counts = seeder.expected_counts(result)
    families = {e.family for e in result.events if not e.is_occurrence}
    assert counts["engaged"] == len(families) > 0
    assert counts["baseline"] == sum(e.kind == "baseline" for e in result.events)
    assert counts["submission"] >= len(result.stages["submitted"])
    # Link-only Families have a session but no baseline; they are engaged.
    linked = set(result.stages["linked"])
    assert linked and linked <= families
    assert not any(e.kind == "baseline" and e.family in linked for e in result.events)


def test_leader_to_import_picks_the_busiest_ministrys_leader():
    """The finish step's leader user leads the Ministry with most open requests.

    Ties go to the lower Ministry DUID and then the first address; with no
    open request anywhere, the first leader by address; no leader, None.
    """
    rows = [
        {"email": "zed@example.test", "ministries": [5, 9]},
        {"email": "amy@example.test", "ministries": [3]},
        {"email": "bob@example.test", "ministries": [9]},
    ]
    assert leader_to_import(rows, {3: 1, 9: 3, 5: 2}) == "bob@example.test"
    assert leader_to_import(rows, {3: 2, 5: 2}) == "amy@example.test"
    # The busiest Ministry has no listed leader: the next one's leader.
    assert leader_to_import(rows, {7: 4, 5: 1}) == "zed@example.test"
    assert leader_to_import(rows, {}) == "amy@example.test"
    assert leader_to_import(rows, {7: 0}) == "amy@example.test"
    assert leader_to_import([], {3: 1}) is None


def test_constants_match_the_specification():
    assert seeder.DEFAULT_WAIT_SECONDS == 600
    assert seeder.STEPS[:7] == (
        "timeline",
        "wizard",
        "campaign",
        "prepare",
        "drive",
        "check",
        "finish",
    )
    # The BG-12 rehearsal's two steps (local/rehearsal.py) follow the seed's,
    # then finish-setup's read-only lookup (#1355), then the Testing snapshot's
    # step, which the operator script looks for.
    assert seeder.STEPS[7:] == ("reminder", "measure", "credentials", "testing")
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


# The Testing snapshot's step (#1413).
@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 10, 15, 15, 30, tzinfo=UTC),
        # Just after and just before midnight on the campaign's wall clock.
        datetime(2026, 10, 16, 4, 1, tzinfo=UTC),
        datetime(2026, 10, 16, 3, 59, tzinfo=UTC),
        # Reminders on Nov 4 and Nov 8, after the Nov 1 change to standard time.
        datetime(2026, 10, 25, 15, 0, tzinfo=UTC),
    ],
)
def test_draft_calendar_contains_now_and_puts_every_schedule_after_it(now):
    """Testing sign-ins need now inside the dates; no schedule may be due yet."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("America/New_York")
    cal = seeder.draft_calendar(now, "America/New_York")
    local = now.astimezone(zone)
    assert cal.start == local.date()
    assert datetime.combine(cal.start, datetime.min.time(), tzinfo=zone) <= now
    assert now < datetime.combine(cal.end, datetime.min.time(), tzinfo=zone)
    initial = datetime.combine(cal.initial, seed_timeline.INITIAL_TIME, tzinfo=zone)
    assert now < initial
    assert all(initial < reminder for reminder in cal.reminders)
    assert list(cal.reminders) == sorted(cal.reminders)
    assert all(r.time() == seed_timeline.REMINDER_TIME for r in cal.reminders)
    assert all(cal.start < r.date() < cal.end for r in cal.reminders)
    assert (cal.end - cal.start).days == seeder.TESTING_CAMPAIGN_DAYS


def test_draft_patch_puts_the_initial_on_its_own_day():
    """``schedule_patch`` with ``initial_date`` adds the Initial on that day."""
    campaign_id = "11111111-1111-1111-1111-111111111111"
    document = {
        "sections": {
            "schedules": [],
            "content": [
                {
                    "id": slot,
                    "values": {
                        "campaign_id": campaign_id,
                        "kind": "email",
                        "slot": slot,
                        "subject": slot.title(),
                    },
                }
                for slot in ("initial", "reminder")
            ],
        }
    }
    cal = seeder.draft_calendar(
        datetime(2026, 10, 15, 15, 30, tzinfo=UTC), "America/New_York"
    )
    patch = schedule_patch(document, campaign_id, cal, initial_date=cal.initial)
    assert patch[0]["values"] == {"start_date": "2026-10-15", "end_date": "2026-11-12"}
    assert patch[1]["operation"] == "add"
    assert patch[1]["values"]["kind"] == "initial"
    assert patch[1]["values"]["date"] == "2026-10-22"
    assert patch[1]["values"]["time"] == "10:00:00"
    assert [(row["values"]["kind"], row["values"]["date"]) for row in patch[2:]] == [
        ("reminder", "2026-10-25"),
        ("reminder", "2026-10-29"),
    ]
    assert {row["values"]["template_version"] for row in patch[2:]} == {"reminder"}


DRAFT = {
    "mode": "testing",
    "state": "draft",
    "went_live": False,
    "schedules": False,
    "recipient": "testing@example.test",
}


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"mode": "production"}, "production mode"),
        ({"state": "scheduled"}, "not a Testing draft"),
        ({"state": "active"}, "not a Testing draft"),
        ({"went_live": True}, "never went live"),
        ({"schedules": True}, "post-setup snapshot"),
        ({"recipient": "office@parish.example.org"}, "@example.test address"),
        ({"recipient": "testing@example.test.example.org"}, "@example.test"),
    ],
)
def test_the_testing_step_refuses_anything_but_a_fresh_synthetic_draft(change, reason):
    """Only a Testing draft without schedules and a reserved recipient passes."""
    assert seeder.draft_refusal(**DRAFT) is None
    assert seeder.draft_refusal(**DRAFT | {"recipient": "Q@Example.Test"}) is None
    refusal = seeder.draft_refusal(**DRAFT | change)
    assert reason in refusal
    # The refusal names no address.
    assert "parish.example.org" not in refusal


def test_the_testing_plan_fits_the_family_test_limit_and_its_answers():
    """Every submitting Family has an answer set; at most ten tests at once."""
    from parishkit.stewardship.jobs.family_mail_models import FAMILY_TEST_LIMIT

    answers = seeder.draft_answers([30, 10, 20])
    assert len(answers) == seeder.TESTING_PLAN.count("submit")
    assert set(seeder.TESTING_PLAN) == {"submit", "open", "sent"}
    assert len(seeder.TESTING_PLAN) <= FAMILY_TEST_LIMIT
    assert answers[0]["ministry_interest"] == [10]
    assert [a["census_edit"] for a in answers] == [
        None,
        "changed_email",
        "proposed_member",
    ]
    assert seeder.draft_answers([])[0]["ministry_interest"] == []


def test_the_testing_step_refuses_outside_local_before_any_connection():
    """The step admits the process as web does, so another profile is refused."""
    from parishkit.stewardship.deployment import DeploymentProfile

    request = SeedRequest.parse(
        SimpleNamespace(
            step="testing",
            seed="7",
            families="100",
            anchor_date="2026-09-20",
            now=NOW,
            response_scale=None,
            clock_dir=None,
            admin_email="admin@example.test",
        )
    )
    with pytest.raises(SeedRefused, match="only in the local profile"):
        seeder.run_step(request, SimpleNamespace(profile=DeploymentProfile.PRODUCTION))


class FakeSeedWeb:
    """The ``seed_web`` calls the testing step makes, recorded in order."""

    SeedWebRefused = seed_web_module.SeedWebRefused

    def __init__(self, calls, document):
        self.calls = calls
        active = SimpleNamespace(digest="digest", document=lambda: document)
        self.service = SimpleNamespace(store=SimpleNamespace(active=lambda: active))
        calls_ = calls

        class Driver:
            """Records each Family's sign-in, form and submission."""

            def __init__(self, service, campaign, epoch_id):
                calls_.append(("driver", epoch_id))

            def session(self, family_id, data):
                calls_.append(("session", family_id))

            def baseline(self, family_id, data):
                calls_.append(("baseline", family_id))

            def submission(self, family_id, data):
                calls_.append(("submission", family_id))

        self.RehearsalFamilyWebDriver = Driver

    def admin_runtime(self):
        return self.service

    def sign_in_admin(self, web, email):
        self.calls.append("sign_in")

    def request_full_refresh(self, web, service):
        self.calls.append("refresh")
        return SimpleNamespace(task_root_id="root")

    def sample_mail(self, web, service, campaign_id, revision):
        self.calls.append("sample")

    def family_tests(self, web, service, campaign_id, revision, duids):
        self.calls.append(("family_tests", tuple(duids)))
        return [SimpleNamespace(pk=duid, rehearsal_epoch_id="epoch") for duid in duids]

    def family_runtime(self):
        return "family-runtime"


def wired(monkeypatch, state):
    """The testing step with every database and web call replaced by a recorder."""
    calls = []
    campaign_id = "11111111-1111-1111-1111-111111111111"
    document = {
        "sections": {
            "schedules": [],
            "content": [
                {
                    "id": slot,
                    "values": {
                        "campaign_id": campaign_id,
                        "kind": "email",
                        "slot": slot,
                        "subject": slot,
                    },
                }
                for slot in ("initial", "reminder")
            ],
        }
    }
    campaign = SimpleNamespace(
        pk=campaign_id,
        state="draft",
        active_configuration=SimpleNamespace(
            timezone="America/New_York", values={"ministry_duids": [7]}
        ),
    )
    fake = FakeSeedWeb(calls, document)
    monkeypatch.setattr(seeder, "seed_web", fake)
    monkeypatch.setattr(seeder, "configure_web_process", lambda c: calls.append("web"))
    monkeypatch.setattr(seeder, "current_campaign", lambda: campaign)
    monkeypatch.setattr(
        seeder,
        "draft_state",
        lambda c, d: calls.append(("state", d is document)) or state,
    )
    monkeypatch.setattr(seeder, "admin", lambda r: calls.append("admin") or "web")
    monkeypatch.setattr(
        seeder, "database_now", lambda: datetime(2026, 10, 15, 15, 30, tzinfo=UTC)
    )
    monkeypatch.setattr(
        seeder,
        "record_configuration",
        lambda web, service, active, patch: calls.append(("configure", len(patch))),
    )
    monkeypatch.setattr(
        seeder, "wait_until", lambda what, condition: calls.append(what) or 0.0
    )
    monkeypatch.setattr(seeder, "settle", lambda since: 0.0)
    monkeypatch.setattr(seeder, "_initial_revision", lambda s, c: "revision")
    monkeypatch.setattr(
        seeder,
        "testing_families",
        lambda c: [(duid, f"family-{duid}") for duid in (1, 2, 3, 4, 5)],
    )
    request = SimpleNamespace(admin_email="admin@example.test")
    return calls, lambda: seeder.testing_step(request, "configuration")


def test_the_testing_step_refuses_before_any_write(monkeypatch):
    """A refusal comes after the reads only: no sign-in, request or send."""
    calls, run = wired(monkeypatch, DRAFT | {"schedules": True})
    with pytest.raises(SeedRefused, match="post-setup snapshot"):
        run()
    assert calls == ["web", ("state", True)]


def test_the_testing_step_runs_its_parts_in_order(monkeypatch):
    """Configuration, readiness, epoch, sends, then each Family's part."""
    calls, run = wired(monkeypatch, DRAFT)
    document = run()
    families = [c for c in calls if isinstance(c, tuple) and c[0] != "state"]
    assert calls[:4] == ["web", ("state", True), "admin", ("configure", 4)]
    order = [c if isinstance(c, str) else c[0] for c in calls]
    expected = [
        "configure",
        "the configuration change request to be installed",
        "refresh",
        "the full ParishSoft refresh to succeed",
        "sample",
        "the Initial email sample to be accepted",
        "the active rehearsal epoch",
        "family_tests",
        "the chosen-Family Testing sends to be delivered",
        "driver",
    ]
    assert [o for o in order if o in set(expected)][: len(expected)] == expected
    # The driver uses the epoch the tickets record.
    assert ("driver", "epoch") in families
    assert families[-8:] == [
        ("session", "family-1"),
        ("submission", "family-1"),
        ("session", "family-2"),
        ("submission", "family-2"),
        ("session", "family-3"),
        ("submission", "family-3"),
        ("session", "family-4"),
        ("baseline", "family-4"),
    ]
    assert (document["submitted"], document["opened"], document["sent_only"]) == (
        [1, 2, 3],
        [4],
        [5],
    )
    assert document["rebuild_before"] == document["initial"] == "2026-10-22"


def test_a_refused_testing_send_is_a_refusal(monkeypatch):
    """The page's unmet precondition exits as refused, not as unexpected."""
    calls, run = wired(monkeypatch, DRAFT)

    def refused(*args):
        raise seed_web_module.SeedWebRefused("unavailable: stale_source")

    monkeypatch.setattr(seeder.seed_web, "family_tests", refused)
    with pytest.raises(SeedRefused, match="stale_source"):
        run()
