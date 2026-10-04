"""The local bulk-send rehearsal (BG-12 PR 1): setting, timing lines and report.

The rehearsal itself runs in the VM (``tools/stewardship-local.sh rehearse``,
documented in the local-environment guide). These tests pin its pure parts:
the local-only SMTP latency setting (refused outside LOCAL, absent from every
other deployment's rendering, waited only by a LOCAL mail parent), the bulk
path's DEBUG timing lines and the report that parses them, the seeder's
Reminder patch and measure document, and the command-line rules.
"""

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import cli
from parishkit.stewardship.deployment import (
    LOCAL_PUBLIC_ORIGIN,
    LOCAL_SMTP_LATENCY_VARIABLE,
    DeploymentProfile,
    load_deployment,
)
from parishkit.stewardship.deployment_documents import deployment_document
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
)
from parishkit.stewardship.family_delivery_process import (
    local_smtp_latency,
    submit_family,
)
from parishkit.stewardship.jobs import family_mail_bulk
from parishkit.stewardship.local import rehearsal_report as report
from parishkit.stewardship.local.rehearsal import measure, reminder_patch
from parishkit.stewardship.local.seeder import (
    MONOTONE_CHECKS,
    SeedRefused,
    SeedRequest,
)
from parishkit.stewardship.mail_catcher import MAIL_CATCHER_DOCUMENT
from parishkit.stewardship.runtime_database import profile_settings
from parishkit.stewardship.runtime_topology import render_runtime

from .test_family_delivery import SETTINGS, sample
from .test_local_profile import (
    LOCAL_IMAGE,
    PRODUCTION_IMAGE,
    canonical,
    configuration_for,
)

LOCAL = DeploymentProfile.LOCAL
PRODUCTION = DeploymentProfile.PRODUCTION
DUE = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


# --- The local-only SMTP latency setting ---------------------------------------


def local_environ(**extra):
    """The environment of a minimal LOCAL deployment, plus ``extra``."""
    return {
        "PARISHKIT_STEWARDSHIP_PROFILE": "local",
        "PARISHKIT_STEWARDSHIP_PUBLIC_ORIGIN": LOCAL_PUBLIC_ORIGIN,
        "PARISHKIT_STEWARDSHIP_TRUSTED_PROXY_HOPS": "1",
        **extra,
    }


def test_the_latency_is_zero_by_default_and_admitted_only_in_local():
    """Default 0; LOCAL takes 0-5000 ms; any other profile refuses a non-zero value."""
    assert load_deployment(environ={}).local_smtp_latency_ms == 0
    assert (
        load_deployment(
            environ=local_environ(**{LOCAL_SMTP_LATENCY_VARIABLE: "600"})
        ).local_smtp_latency_ms
        == 600
    )
    # Compose passes variables empty unless exported: empty means unset.
    assert (
        load_deployment(environ={LOCAL_SMTP_LATENCY_VARIABLE: ""}).local_smtp_latency_ms
        == 0
    )
    # Zero is the default everywhere, so stating it is harmless.
    assert (
        load_deployment(
            environ={LOCAL_SMTP_LATENCY_VARIABLE: "0"}
        ).local_smtp_latency_ms
        == 0
    )
    with pytest.raises(ConfigError, match="only in the local profile"):
        load_deployment(environ={LOCAL_SMTP_LATENCY_VARIABLE: "600"})
    for value in ("5001", "-1", "x", "0.5"):
        with pytest.raises(ConfigError, match="local_smtp_latency_ms"):
            load_deployment(
                environ=local_environ(**{LOCAL_SMTP_LATENCY_VARIABLE: value})
            )


def test_production_rendering_is_unaffected_by_the_latency_setting(tmp_path):
    """Production renders nothing of the setting, so its output is unchanged.

    The loader refuses any non-zero value outside LOCAL, so 0 is the only
    value a Production deployment can hold, and with 0 no rendered Compose
    service, document or Django setting names it. (The Production golden
    files in test_local_profile pin the rendering itself byte for byte.)
    """
    production = configuration_for(PRODUCTION, tmp_path)
    assert production.local_smtp_latency_ms == 0
    compose, documents = render_runtime(production, image=PRODUCTION_IMAGE)
    rendered = canonical(compose) + "".join(
        json.dumps(value, sort_keys=True, default=str) for value in documents.values()
    )
    assert "latency" not in rendered.lower()
    assert "local_smtp_latency_ms" not in deployment_document(production)["deployment"]
    assert "STEWARDSHIP_LOCAL_SMTP_LATENCY_MS" not in profile_settings(production)


def test_a_local_latency_is_rendered_but_never_recorded_as_an_input(tmp_path):
    """LOCAL renders the value into every document; the provisioning record omits it."""
    local = replace(configuration_for(LOCAL, tmp_path), local_smtp_latency_ms=600)
    _, documents = render_runtime(local, image=LOCAL_IMAGE)
    mail = [
        value
        for path, value in documents.items()
        if str(path).endswith("mail-dispatch.yaml")
    ]
    assert mail and mail[0]["deployment"]["local_smtp_latency_ms"] == 600
    assert deployment_document(local, switches=False) == deployment_document(
        replace(local, local_smtp_latency_ms=0)
    )
    assert profile_settings(local)["STEWARDSHIP_LOCAL_SMTP_LATENCY_MS"] == 600


def test_only_a_local_parent_waits(settings):
    """The wait is the LOCAL setting in seconds; every other profile never waits."""
    settings.STEWARDSHIP_LOCAL_SMTP_LATENCY_MS = 600
    assert local_smtp_latency(LOCAL) == 0.6
    assert local_smtp_latency(PRODUCTION) == 0.0
    del settings.STEWARDSHIP_LOCAL_SMTP_LATENCY_MS
    assert local_smtp_latency(LOCAL) == 0.0


def test_a_local_submission_waits_before_the_helper(settings, monkeypatch):
    """The wait comes first and is outside the helper exchange (so in submit_ms)."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = "local"
    settings.STEWARDSHIP_LOCAL_SMTP_LATENCY_MS = 250
    events = []
    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process.time.sleep",
        lambda seconds: events.append(("sleep", seconds)),
    )

    def exchange(payload, **kwargs):
        events.append(("helper", None))
        return kwargs["decode"](
            json.dumps(
                FamilyDeliveryResult(FamilyDeliveryStatus.ACCEPTED, 2).payload()
            ).encode()
        )

    monkeypatch.setattr(
        "parishkit.stewardship.family_delivery_process._submit_private", exchange
    )
    result = submit_family(
        MAIL_CATCHER_DOCUMENT, SETTINGS, sample(), seconds=5, check=lambda: None
    )
    assert result.status is FamilyDeliveryStatus.ACCEPTED
    assert events == [("sleep", 0.25), ("helper", None)]


# --- The bulk path's timing lines ----------------------------------------------


def test_a_timing_line_round_trips_into_the_report(caplog):
    """What _timing logs is exactly what parse_timings reads back."""
    pace = family_mail_bulk._Pace()
    pace.durations = [0.2, 0.3]
    with caplog.at_level(logging.DEBUG, logger="parishkit.stewardship.debug"):
        family_mail_bulk._timing("commit", pace, items=2, tried=3, work=[0.15])
    message = caplog.records[-1].getMessage()
    line = json.dumps({"timestamp": "t", "extra": {"debug": {"message": message}}})
    bulk, renewals = report.parse_timings([line])
    assert renewals == []
    assert bulk[0]["kind"] == "commit" and bulk[0]["items"] == 2
    assert bulk[0]["tried"] == 3 and bulk[0]["item_ms"] == [200, 300]
    assert bulk[0]["work_ms"] == [150]
    assert bulk[0]["prebuilt"] == 0 and bulk[0]["rebuilt"] == 0
    assert bulk[0]["timestamp"] == "t"


def test_the_real_formatter_line_parses(monkeypatch):
    """A timing line through SafeJsonFormatter (debug logging on) parses back."""
    from parishkit.stewardship.observability import (
        DEBUG_LOGGING_VARIABLE,
        SafeJsonFormatter,
    )

    monkeypatch.setenv(DEBUG_LOGGING_VARIABLE, "1")
    pace = family_mail_bulk._Pace()
    pace.durations = [0.4]
    records = []

    class Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Keep(level=logging.DEBUG)
    logger = logging.getLogger("parishkit.stewardship.debug")
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        family_mail_bulk._timing("prepare", pace, items=1, tried=1, work=[0.3])
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    line = SafeJsonFormatter().format(records[-1])
    bulk, _ = report.parse_timings([line])
    assert bulk[0]["kind"] == "prepare" and bulk[0]["item_ms"] == [400]
    assert bulk[0]["work_ms"] == [300] and bulk[0]["timestamp"]


def test_a_timing_failure_never_reaches_the_batch(monkeypatch):
    """Anything going wrong while logging a timing line is swallowed."""

    class Broken:
        durations = [0.1]

        def held(self):
            raise RuntimeError("clock gone")

    monkeypatch.setattr(family_mail_bulk.DEBUG, "isEnabledFor", lambda level: True)
    family_mail_bulk._timing("commit", Broken(), items=1, tried=1)


def test_pace_records_each_item_time():
    """_Pace keeps each item's seconds beside its running total."""
    pace = family_mail_bulk._Pace()
    pace.ran(family_mail_bulk.monotonic())
    pace.ran(family_mail_bulk.monotonic())
    assert len(pace.durations) == 2 and pace.count == 2
    assert abs(sum(pace.durations) - pace.spent) < 1e-9


# --- The report's parsing and arithmetic ---------------------------------------


def debug_line(message, timestamp="2026-10-06T12:00:01+00:00"):
    """One service JSON log line carrying a DEBUG message."""
    return json.dumps(
        {
            "timestamp": timestamp,
            "level": "DEBUG",
            "extra": {"debug": {"logger": "x", "message": message}},
        }
    )


def test_percentiles_use_the_nearest_rank():
    """Every number reported is one that was measured."""
    assert report.percentiles([]) == {"n": 0}
    values = list(range(1, 101))
    assert report.percentiles(values) == {
        "n": 100,
        "p50": 50,
        "p90": 90,
        "p99": 99,
        "max": 100,
    }
    assert report.percentiles([7]) == {"n": 1, "p50": 7, "p90": 7, "p99": 7, "max": 7}


def test_timing_parsing_skips_everything_else():
    """Non-JSON, other messages, unknown kinds and malformed payloads are skipped."""
    lines = [
        "not json",
        json.dumps({"extra": {}}),
        debug_line("Timer wake-up!"),
        debug_line('bulk timing: {"kind": "other"}'),
        debug_line("bulk timing: {broken"),
        debug_line('lease renewal timing: {"wait_ms": "slow"}'),
        debug_line('bulk timing: {"kind": "prepare", "items": 1, "hold_ms": 800}'),
        debug_line('lease renewal timing: {"wait_ms": 12}'),
    ]
    bulk, renewals = report.parse_timings(lines)
    assert [row["kind"] for row in bulk] == ["prepare"] and renewals == [12]


def sample_line(epoch, holders, waiters, holder="-", waiting="-", idle=0, busy=0):
    """One sampler line; CPU counters grow by ``busy`` and ``idle`` jiffies."""
    cpu = f"{100 + busy} 0 0 {1000 + idle} 0 0 0 0"
    return f"{epoch}\t{holders}\t{waiters}\t{holder}\t{waiting}\t{cpu}\t1.5"


def test_lock_samples_give_held_and_waited_shares_and_cpu():
    """Shares, holder states and roles, waiter roles, CPU busy share and load."""
    lines = [
        sample_line(1.0, 1, 2, "pk_worker:idle_in_transaction", "pk_mail,pk_web"),
        sample_line(2.0, 1, 0, "pk_mail:active", busy=50, idle=50),
        sample_line(3.0, 0, 0, busy=75, idle=125),
        "garbage",
        "1\t2\t3",
    ]
    samples = report.parse_samples(lines)
    assert len(samples) == 3
    summary = report.lock_summary(samples)
    assert summary["samples"] == 3
    assert summary["held_share"] == round(2 / 3, 3)
    assert summary["waited_share"] == round(1 / 3, 3)
    assert summary["max_waiters"] == 2
    assert summary["holder_states"] == {"active": 1, "idle_in_transaction": 1}
    assert summary["holder_roles"] == {"pk_mail": 1, "pk_worker": 1}
    assert summary["waiter_roles"] == {"pk_mail": 1, "pk_web": 1}
    # 75 busy of 200 jiffies between the first and last samples.
    assert summary["cpu_busy"] == 0.375
    assert report.lock_summary([]) == {"samples": 0}
    assert report.cpu_busy(samples[:1]) is None


def test_holds_are_summarized_per_kind():
    """Batches, items, tried, holds, item and work times, prebuilt and rebuilt."""
    bulk = [
        {"kind": "prepare", "items": 1, "tried": 1, "hold_ms": 800, "item_ms": [790]},
        {
            "kind": "prepare",
            "items": 2,
            "tried": 3,
            "hold_ms": 700,
            "item_ms": [300, 300, 90],
            "work_ms": [200, 210],
            "prebuilt": 2,
            "rebuilt": 1,
        },
        {
            "kind": "outcome",
            "items": 5,
            "tried": 5,
            "hold_ms": 400,
            "item_ms": [80] * 5,
        },
    ]
    summary = report.hold_summary(bulk)
    assert set(summary) == {"prepare", "outcome"}
    prepare = summary["prepare"]
    assert prepare["batches"] == 2 and prepare["items"] == 3 and prepare["tried"] == 4
    assert prepare["items_per_batch"] == 1.5
    assert prepare["hold_ms"]["max"] == 800 and prepare["item_ms"]["n"] == 4
    assert prepare["work_ms"]["p50"] == 200
    assert prepare["prebuilt"] == 2 and prepare["rebuilt"] == 1
    assert summary["outcome"]["work_ms"] == {"n": 0}


def outcome(message, settled, submitted, reason="smtp_accepted", **stats):
    """One measured outcome row."""
    return {
        "message_id": message,
        "settled_at": settled.isoformat(),
        "submitted_at": submitted.isoformat(),
        "reason": reason,
        "state": "delivered",
        "stats": stats or None,
    }


def measured(count=4, *, two=0, unknown=0, pending=0, twice=0, invariants=None):
    """A measure document for ``count`` candidate Families, sent two a minute."""
    occurrences, messages, outcomes = [], [], []
    for index in range(count):
        state = "succeeded"
        if index < unknown:
            state = "delivery_unknown"
        elif index < unknown + pending:
            state = "running"
        occurrences.append({"id": f"o{index}", "state": state, "created_at": None})
        created = DUE + timedelta(seconds=10 * (index + 1))
        messages.append(
            {
                "id": f"m{index}",
                "occurrence_id": f"o{index}",
                "state": "delivery_unknown" if index < unknown else "delivered",
                "created_at": created.isoformat(),
                "finished_at": None,
            }
        )
        if index >= unknown + pending:
            settled = DUE + timedelta(seconds=30 * (index + 1))
            outcomes.append(
                outcome(
                    f"m{index}",
                    settled,
                    settled - timedelta(seconds=1),
                    submit_ms=600 + index,
                    smtp_ms=5,
                )
            )
    # A message accepted a second time (it reached the provider twice).
    outcomes += [dict(outcomes[index]) for index in range(twice)]
    occurrences.append({"id": "skip", "state": "skipped", "created_at": None})
    return {
        "step": "measure",
        "due_at": DUE.isoformat(),
        "kinds": ["reminder"],
        "modes": ["production"],
        "targets": count + 1,
        "occurrences": occurrences,
        "messages": messages,
        "outcomes": outcomes,
        "targets_with_two_messages": two,
        "targets_with_two_fulfillments": 0,
        "failed_tasks": [],
        "invariant_violations": invariants or {},
    }


def test_a_clean_send_passes_with_its_rates_and_phases():
    """Due to last outcome, accepted a minute, preparation and phase percentiles."""
    meta = {"due_at": DUE.isoformat(), "deadlocks_before": 3, "deadlocks_after": 3}
    send = report.send_summary(meta, measured())
    # Four outcomes, the last 120 s after the due time: 2 a minute.
    assert send["due_to_last_outcome_minutes"] == 2.0
    assert send["accepted_per_minute"] == 2.0
    # The last message was created 40 s after the due time: 6 a minute.
    assert send["preparation_minutes"] == 0.67
    assert send["prepared_per_minute"] == 6.0
    assert send["send_only_minutes"] is None
    assert send["phases"]["submit_ms"]["p50"] == 601
    assert send["phases"]["wait_ms"] == {"n": 0}
    correct = send["correctness"]
    assert correct["passed"] and correct["candidates"] == 4
    assert correct["deadlocks"] == 0


def test_a_send_only_run_measures_from_the_dispatch_restart():
    """With mail-dispatch held, the send rate starts when it starts again."""
    meta = {
        "due_at": DUE.isoformat(),
        "dispatch_started_at": (DUE + timedelta(seconds=60)).isoformat(),
    }
    send = report.send_summary(meta, measured())
    assert send["send_only_minutes"] == 1.0 and send["send_only_per_minute"] == 4.0


@pytest.mark.parametrize(
    "document, meta",
    [
        (measured(two=1), {}),
        (measured(unknown=1), {}),
        (measured(pending=1), {}),
        (measured(), {"timed_out": True}),
        (measured(count=0), {}),
        (measured(twice=1), {}),
        (measured(invariants={"messages before their occurrence": 1}), {}),
    ],
)
def test_any_correctness_fault_fails_the_run(document, meta):
    """A second message, an unknown, unfinished work, a timeout, no candidates,
    a message accepted twice or a broken ordering invariant."""
    meta = {"due_at": DUE.isoformat(), **meta}
    assert report.send_summary(meta, document)["correctness"]["passed"] is False


def test_accepted_messages_are_counted_once_and_unknowns_once_each():
    """Two acceptances of one message count as one; an unknown is one per occurrence."""
    correct = report.send_summary({}, measured(twice=1))["correctness"]
    assert correct["accepted"] == 4 and correct["accepted_twice"] == 1
    # The occurrence and its message both say delivery_unknown: one unknown.
    correct = report.send_summary({}, measured(unknown=2))["correctness"]
    assert correct["delivery_unknown"] == 2


def test_the_report_reads_a_run_directory_and_writes_its_summary(tmp_path, capsys):
    """The console entry prints every section and keeps summary.json."""
    meta = {
        "label": "baseline",
        "image": "img",
        "families": 100,
        "bulk": True,
        "smtp_latency_ms": 600,
        "mail_consumers": 2,
        "send_only": False,
        "due_at": DUE.isoformat(),
        "deadlocks_before": 0,
        "deadlocks_after": 1,
        "mailpit_before": 10,
        "mailpit_after": 14,
    }
    (tmp_path / "meta.json").write_text(json.dumps(meta))
    (tmp_path / "measure.json").write_text(json.dumps(measured()))
    (tmp_path / "timings.jsonl").write_text(
        debug_line(
            'bulk timing: {"kind": "commit", "items": 2, "tried": 2, '
            '"hold_ms": 500, "item_ms": [250, 240], "work_ms": [120, 110], '
            '"prebuilt": 0, "rebuilt": 0}'
        )
        + "\n"
        + debug_line('lease renewal timing: {"wait_ms": 40}')
        + "\n"
    )
    (tmp_path / "locks.tsv").write_text(
        sample_line(1.0, 1, 1, "pk_mail:active", "pk_worker") + "\n"
    )
    assert report.execute_rehearsal_report(str(tmp_path)) == 0
    text = capsys.readouterr().out
    for fragment in (
        "Rehearsal baseline",
        "Due to last outcome: 2.0 min, 2.0 accepted/min",
        "commit: 1 batches, 2 items",
        "Lease renewal waits (ms): n=1",
        "deadlocks (40P01) 1",
        "Mailpit 4",
        "Correctness PASSED",
    ):
        assert fragment in text, fragment
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["send"]["correctness"]["deadlocks"] == 1
    assert summary["holds"]["commit"]["work_ms"]["max"] == 120


def test_a_run_directory_without_its_records_is_refused(tmp_path, capsys):
    """meta.json and measure.json are required; the others are optional."""
    assert report.execute_rehearsal_report(str(tmp_path)) == 2
    assert "meta.json and measure.json" in capsys.readouterr().err


# --- The seeder's rehearsal steps -----------------------------------------------


def configuration_document(campaign_id):
    """A configuration document with one Initial schedule and a Reminder email."""
    campaign_id = str(campaign_id)
    return {
        "sections": {
            "schedules": [
                {
                    "id": "initial",
                    "values": {
                        "campaign_id": campaign_id,
                        "kind": "initial",
                        "subject": "Welcome",
                        "template_version": "initial-email",
                    },
                }
            ],
            "content": [
                {
                    "id": "reminder-email",
                    "values": {
                        "campaign_id": campaign_id,
                        "kind": "email",
                        "slot": "reminder",
                        "subject": "A reminder",
                    },
                }
            ],
        }
    }


def test_the_reminder_patch_adds_one_schedule_on_the_campaign_wall_clock():
    """12:00 UTC on 2026-10-06 is 08:00 in New York; the Reminder email is named."""
    campaign = uuid4()
    patch, schedule_id = reminder_patch(
        configuration_document(campaign), campaign, DUE, "America/New_York"
    )
    assert patch == [
        {
            "operation": "add",
            "section": "schedules",
            "id": schedule_id,
            "values": {
                "campaign_id": str(campaign),
                "kind": "reminder",
                "date": "2026-10-06",
                "time": "08:00:00",
                "weekday": None,
                "subject": "A reminder",
                "template_version": "reminder-email",
            },
        }
    ]


def seed_args(step, **extra):
    """The command-line namespace the operator script gives every seeder step."""
    values = {
        "step": step,
        "seed": "1",
        "families": "100",
        "anchor_date": "2026-09-17",
        "now": "2026-10-04T12:00:00+00:00",
        "response_scale": None,
        "clock_dir": None,
        "admin_email": "admin@example.test",
        "seeded_now": None,
        "due_at": None,
    }
    return SimpleNamespace(**(values | extra))


def test_the_rehearsal_steps_need_a_whole_minute_due_instant():
    """--due-at is required by reminder and measure, zoned and on a minute."""
    request = SeedRequest.parse(seed_args("reminder", due_at="2026-10-06T08:00-04:00"))
    assert request.due_at == DUE
    for step in ("reminder", "measure"):
        for value in (None, "2026-10-06T12:00", "2026-10-06T12:00:30Z", "soon"):
            with pytest.raises(SeedRefused, match="--due-at"):
                SeedRequest.parse(seed_args(step, due_at=value))
    # The seed's own steps neither need nor mind it.
    assert SeedRequest.parse(seed_args("timeline")).due_at is None


class FakeCursor:
    """A cursor that answers each fetchall with the next canned rows.

    ``fetchone`` (the ordering-invariant counts) answers ``invariant``.
    """

    def __init__(self, *answers, invariant=0):
        self.answers = list(answers)
        self.statements = []
        self.invariant = invariant

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def fetchall(self):
        return self.answers.pop(0)

    def fetchone(self):
        return (self.invariant,)


def test_measure_counts_messages_outcomes_and_second_messages():
    """The measure document for two Families, one of them mailed twice."""
    created = DUE - timedelta(minutes=1)
    occurrences = [uuid4(), uuid4(), uuid4()]
    messages = [uuid4(), uuid4(), uuid4()]
    rows = [
        (occurrences[0], "f1", "succeeded", "production", created, messages[0],
         "delivered", DUE, DUE, "reminder"),
        (occurrences[1], "f2", "succeeded", "production", created, messages[1],
         "delivered", DUE, DUE, "reminder"),
        (occurrences[2], "f2", "succeeded", "production", created, messages[2],
         "delivered", DUE, DUE, "reminder"),
    ]  # fmt: skip
    outcomes = [
        (messages[0], DUE, DUE, "smtp_accepted", "delivered", {"submit_ms": 600}),
        (messages[1], DUE, DUE, "smtp_accepted", "delivered", '{"submit_ms": 610}'),
    ]
    cursor = FakeCursor(rows, outcomes, [("f2", 2)], [("outbox_delivery", "failed", 1)])
    document = measure(cursor, uuid4(), DUE)
    assert document["kinds"] == ["reminder"] and document["modes"] == ["production"]
    assert document["targets"] == 2 and len(document["messages"]) == 3
    assert document["targets_with_two_messages"] == 1
    assert document["targets_with_two_fulfillments"] == 1
    assert [row["stats"] for row in document["outcomes"]] == [
        {"submit_ms": 600},
        {"submit_ms": 610},
    ]
    assert document["failed_tasks"] == [
        {"task_type": "outbox_delivery", "state": "failed", "count": 1}
    ]
    # Message and occurrence ids reach the queries as UUIDs (uuid = ANY(uuid[])).
    assert cursor.statements[1][1] == [messages]
    assert cursor.statements[2][1] == [occurrences]
    # Every statement is a read.
    assert all(
        sql.lstrip().upper().startswith("SELECT") for sql, _ in cursor.statements
    )
    assert document["invariant_violations"] == {}
    assert document["messages"][0]["occurrence_id"] == str(occurrences[0])
    json.dumps(document)


def test_measure_reports_broken_ordering_invariants():
    """Each of the seed's ordering checks that counts rows is named."""
    cursor = FakeCursor([], [], [], invariant=2)
    document = measure(cursor, uuid4(), DUE)
    assert set(document["invariant_violations"]) == {
        label for _, label in MONOTONE_CHECKS
    }


# --- Command line -------------------------------------------------------------


def test_the_report_command_takes_only_its_input(capsys):
    """local-rehearsal-report needs --input and refuses other options."""
    with pytest.raises(SystemExit):
        cli.main(["local-rehearsal-report"])
    with pytest.raises(SystemExit):
        cli.main(["local-rehearsal-report", "--input", "x", "--step", "reminder"])
    assert "due_at" in cli._COMMAND_OPTIONS["local-seed"]


def test_a_failed_or_timed_out_run_exits_one(tmp_path, capsys):
    """The report's exit status is the run's verdict (the script passes it on)."""
    (tmp_path / "meta.json").write_text(
        json.dumps({"due_at": DUE.isoformat(), "timed_out": True})
    )
    (tmp_path / "measure.json").write_text(json.dumps(measured()))
    assert report.execute_rehearsal_report(str(tmp_path)) == 1
    assert "Correctness FAILED" in capsys.readouterr().out
