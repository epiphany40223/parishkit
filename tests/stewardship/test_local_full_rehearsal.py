"""The full-size local rehearsal (BG-12 PR 4, #447): its pure parts.

The rehearsal runs in a throwaway local VM (``tools/stewardship-local.sh
rehearse --full`` and ``compare``, documented in the local-environment
guide). These tests pin what runs without a deployment: the Family probe's
choice of Family, its pacing, stop file and logged deadline; the Mailpit
audit's counts; the report's full-size measurements and stricter
correctness; the side-by-side comparison; and the command-line rules.
"""

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from types import SimpleNamespace

import pytest

from parishkit.stewardship import cli
from parishkit.stewardship.local import rehearsal_report as report
from parishkit.stewardship.local import send_probe
from parishkit.stewardship.local.rehearsal import CREDENTIALS_SQL
from parishkit.stewardship.local.seeder import DUE_STEPS, STEPS

from .test_local_rehearsal import DUE, debug_line, measured

START = datetime(2026, 10, 6, 11, 55, tzinfo=UTC)


# --- The Family probe ----------------------------------------------------------


def mail(link):
    """A Mailpit message carrying one Family link (or none)."""
    text = f"Open {send_probe.spike.ORIGIN}/access/{link} now" if link else "no link"
    return {"Text": text, "HTML": None}


def test_the_probe_takes_the_newest_message_it_has_not_used():
    """Newest first; a used message or an already probed link is skipped."""
    bodies = {"1": mail("tokenAAAA"), "2": mail("tokenBBBB"), "3": mail("tokenAAAA")}
    listing = [("1", START), ("2", START), ("3", START)]
    used = {"messages": set(), "tokens": set()}
    # Message 3 is newest, and its link has not been probed yet.
    assert send_probe.newest_unused(listing, used, bodies.get) == "tokenAAAA"
    # Then 2; 1 carries the link already probed, so nothing is left.
    assert send_probe.newest_unused(listing, used, bodies.get) == "tokenBBBB"
    assert send_probe.newest_unused(listing, used, bodies.get) is None


class Clock:
    """A clock that moves only when the probe pauses or a Family runs."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def pause(self, seconds):
        self.now += seconds


def test_the_probe_paces_itself_and_stops_at_the_stop_file(tmp_path):
    """One Family per interval, one JSON line each, until the stop file appears."""
    clock = Clock()
    stop = tmp_path / ".stop"
    ran = []

    def family(_browser, token):
        ran.append((clock.now, token))
        clock.now += 1.5
        if len(ran) == 3:
            stop.touch()
        return {"access": (0.25, "ok"), "form": (1.0, "ok"), "submit": (None, "error")}

    messages = [(str(n), START) for n in range(10)]
    out = StringIO()
    count = send_probe.run_probe(
        since=START,
        every=10,
        wait_seconds=600,
        context=None,
        stop_file=stop,
        fetch=lambda since: messages,
        message=lambda identifier: mail(f"token{identifier}xxxx"),
        family=family,
        browser=lambda context: None,
        clock=clock,
        pause=clock.pause,
        out=out,
    )
    assert count == 3
    # Ten seconds apart, newest message first.
    assert [round(at) for at, _ in ran] == [0, 10, 20]
    assert [token for _, token in ran] == ["token9xxxx", "token8xxxx", "token7xxxx"]
    line = json.loads(out.getvalue().splitlines()[0])
    assert line["steps"]["form"] == {"seconds": 1.0, "outcome": "ok"}
    assert line["steps"]["submit"] == {"seconds": None, "outcome": "error"}
    # Never a token or link in the output.
    assert "token" not in out.getvalue() and "access/" not in out.getvalue()


def test_the_probe_logs_its_deadline_and_skips_when_no_mail_is_new(tmp_path, capsys):
    """No unused mail runs nobody; the wait limit is logged with its numbers."""
    clock = Clock()
    count = send_probe.run_probe(
        since=START,
        every=5,
        wait_seconds=30,
        context=None,
        stop_file=tmp_path / ".stop",
        fetch=lambda since: [],
        message=lambda identifier: {},
        family=lambda *_: pytest.fail("no Family should run"),
        browser=lambda context: None,
        clock=clock,
        pause=clock.pause,
        out=StringIO(),
    )
    assert count == 0
    err = capsys.readouterr().err
    assert "TIMEOUT: the send probe after 30.2s (limit 30s)" in err


def test_the_audit_counts_links_twice_and_links_never_emailed_before():
    """History links before the run; the run's links counted per message."""
    earlier = START - timedelta(days=3)
    listing = [
        ("h1", earlier),
        ("h2", earlier),
        ("r1", START),
        ("r2", START + timedelta(seconds=5)),
        ("r3", START + timedelta(seconds=9)),
        ("r4", START + timedelta(seconds=12)),
    ]
    bodies = {
        "h1": mail("tokenAAAA"),
        "h2": mail("tokenBBBB"),
        "r1": mail("tokenAAAA"),
        "r2": mail("tokenAAAA"),
        "r3": mail("tokenNEWW"),
        "r4": mail(None),
    }
    document = send_probe.audit(START, listing=lambda: listing, message=bodies.get)
    assert document == {
        "check": "mail-audit",
        "history_links": 2,
        "run_messages": 4,
        "run_messages_without_link": 1,
        "run_links": 2,
        "links_in_two_messages": 1,
        "links_not_emailed_before": 1,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"profile": "production"},
        {"since": "yesterday"},
        {"every": "1"},
        {"every": "601"},
        {"wait_seconds": "5"},
        {"stop_file": None},
        {"ca_file": "/nonexistent/root.crt"},
    ],
)
def test_the_probe_refuses_bad_options_before_any_request(overrides, capsys):
    """Only --profile local, a UTC instant and bounded numbers start a probe."""
    args = SimpleNamespace(
        profile="local",
        since=START.isoformat(),
        every="15",
        wait_seconds="600",
        stop_file="/rehearsal/.stop",
        ca_file="/nonexistent/root.crt",
    )
    for name, value in {"ca_file": __file__, **overrides}.items():
        setattr(args, name, value)
    assert send_probe.execute_send_probe(args) == 2
    assert capsys.readouterr().err.startswith("ERROR: ")


def test_the_audit_refuses_outside_local(capsys):
    """The audit names fixed LOCAL endpoints; another profile is refused."""
    args = SimpleNamespace(profile="production", since=START.isoformat())
    assert send_probe.execute_mail_audit(args) == 2


# --- The report's full-size part -----------------------------------------------

CREDENTIALS = "family_codes\t1100\tabc\nlink_tokens\t990\tdef\n"


def full_meta(**extra):
    """A full-size run's meta.json."""
    return {
        "label": "candidate",
        "image": "parishkit-stewardship-local:abc-1",
        "families": 1100,
        "due_at": DUE.isoformat(),
        "deadlocks_before": 0,
        "deadlocks_after": 0,
        "pause_after": 2,
        "pause_seconds": 60,
        "probe_every": 15,
        **extra,
    }


def write_run(directory, *, meta=None, after=CREDENTIALS, audit=None, pause=True):
    """A complete full-size run directory: four sent, paused after two."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "meta.json").write_text(json.dumps(meta or full_meta()))
    # Outcomes settle 30, 60, 90 and 120 s after the due time.
    (directory / "measure.json").write_text(json.dumps(measured()))
    (directory / "timings.jsonl").write_text(
        "\n".join(
            debug_line("bulk timing: " + json.dumps(line))
            for line in (
                {"kind": "prepare", "items": 4, "tried": 4, "hold_ms": 200},
                {"kind": "commit", "items": 2, "tried": 2, "hold_ms": 300},
                {"kind": "commit", "items": 2, "tried": 2, "hold_ms": 100},
                {"kind": "outcome", "items": 4, "tried": 4, "hold_ms": 120},
            )
        )
        + "\n"
    )
    (directory / "credentials-before.tsv").write_text(CREDENTIALS)
    (directory / "credentials-after.tsv").write_text(after)
    audit = audit or {
        "check": "mail-audit",
        "history_links": 990,
        "run_messages": 5,
        "run_messages_without_link": 1,
        "run_links": 4,
        "links_in_two_messages": 0,
        "links_not_emailed_before": 0,
    }
    (directory / "audit.json").write_text(json.dumps(audit))
    probe = {
        "at": DUE.isoformat(),
        "steps": {
            "access": {"seconds": 0.2, "outcome": "ok"},
            "form": {"seconds": 1.4, "outcome": "ok"},
            "submit": {"seconds": 0.9, "outcome": "ok"},
        },
    }
    slow = {
        "at": DUE.isoformat(),
        "steps": {"form": {"seconds": None, "outcome": "timeout"}},
    }
    (directory / "probe.jsonl").write_text(
        json.dumps(probe) + "\n" + json.dumps(slow) + "\nnot json\n"
    )
    if pause:
        (directory / "pause.json").write_text(
            json.dumps(
                {
                    "confirmed_at": (DUE + timedelta(seconds=65)).isoformat(),
                    "paused": True,
                }
            )
        )
        (directory / "resume.json").write_text(
            json.dumps(
                {
                    "confirmed_at": (DUE + timedelta(seconds=100)).isoformat(),
                    "paused": False,
                    "quiet_seconds": 3.0,
                    "test_seconds": 4.0,
                }
            )
        )
    return directory


def summary_of(directory):
    """The summary the report writes for one run directory."""
    report.execute_rehearsal_report(str(directory))
    return json.loads((directory / "summary.json").read_text())


def test_a_full_size_run_reports_lock_probes_pause_and_audits(tmp_path, capsys):
    """Lock per message, probe percentiles, pause timings and both audits."""
    run = write_run(tmp_path / "run")
    assert report.execute_rehearsal_report(str(run)) == 0
    text = capsys.readouterr().out
    full = json.loads((run / "summary.json").read_text())["full"]
    # 200 + 400 + 120 ms of holds over four accepted messages.
    assert full["lock_per_message"] == {
        "total_ms_per_message": 180.0,
        "by_kind_ms_per_message": {"prepare": 50.0, "commit": 100.0, "outcome": 30.0},
    }
    probes = full["probes"]
    assert probes["probes"] == 2 and probes["failed_probes"] == 1
    assert probes["steps"]["form"]["seconds"]["max"] == 1.4
    assert probes["steps"]["form"]["outcomes"] == {"ok": 1, "timeout": 1}
    pause = full["pause"]
    # Outcomes at 30 and 60 s, then 90 s while paused (65 to 100 s), then 120.
    assert pause["outcomes_before_pause"] == 2
    assert pause["settled_while_paused"] == 1
    assert pause["outcomes_after_resume"] == 1
    assert pause["paused_seconds"] == 35.0
    assert pause["pause_to_quiet_seconds"] == 25.0
    assert pause["resume_to_first_outcome_seconds"] == 20.0
    # Two minutes from due to the last outcome, 35 s of it paused.
    assert full["send_minutes_without_pause"] == 1.42
    assert full["credentials"]["unchanged"] is True
    correct = json.loads((run / "summary.json").read_text())["send"]["correctness"]
    assert correct["passed"] and correct["pause_resumed"]
    for fragment in (
        "Lock held per accepted message: 180.0 ms",
        "Family probes: 2 (1 with a step that did not answer ok)",
        "1 settled while paused",
        "Credentials (codes, code MACs, link tokens, generations, rehearsal "
        "credentials): unchanged",
        "Mailpit audit: 5 messages since the run began, 4 Family links",
        "Correctness PASSED",
    ):
        assert fragment in text, fragment


@pytest.mark.parametrize(
    "options",
    [
        {"after": "family_codes\t1100\tabc\nlink_tokens\t990\tCHANGED\n"},
        {"after": ""},
        {
            "audit": {
                "run_messages": 4,
                "run_messages_without_link": 0,
                "run_links": 3,
                "links_in_two_messages": 1,
                "links_not_emailed_before": 0,
            }
        },
        {
            "audit": {
                "run_messages": 4,
                "run_messages_without_link": 0,
                "run_links": 4,
                "links_in_two_messages": 0,
                "links_not_emailed_before": 1,
            }
        },
        {"pause": False},
    ],
)
def test_a_changed_credential_a_second_link_or_no_pause_fails(tmp_path, options):
    """Any credential change, a link twice, a new link or a missing pause fails."""
    run = write_run(tmp_path / "run", **options)
    assert report.execute_rehearsal_report(str(run)) == 1
    assert summary_of(run)["send"]["correctness"]["passed"] is False


def test_a_run_without_full_size_inputs_reports_as_before(tmp_path):
    """The PR 1 rehearsal's directory has no full section and the same checks."""
    meta = {"due_at": DUE.isoformat()}
    summary = report.summarize(meta, measured(), [], [])
    assert "full" not in summary
    assert "credentials_unchanged" not in summary["send"]["correctness"]


def test_two_runs_print_side_by_side(tmp_path, capsys):
    """compare reads both summaries; its exit says whether both passed."""
    baseline = write_run(tmp_path / "a", meta=full_meta(label="release"))
    candidate = write_run(tmp_path / "b", meta=full_meta(label="pr-1373"))
    summary_of(baseline)
    summary_of(candidate)
    capsys.readouterr()
    assert report.execute_rehearsal_compare(str(baseline), str(candidate)) == 0
    text = capsys.readouterr().out
    assert text.splitlines()[0].split() == ["release", "pr-1373"]
    assert any(
        line.startswith("Lock held per message (ms)") for line in text.splitlines()
    )
    row = next(line for line in text.splitlines() if line.startswith("Accepted "))
    assert row.split()[-2:] == ["4", "4"]
    # A failed side fails the comparison; a missing summary is refused.
    failed = write_run(tmp_path / "c", pause=False)
    summary_of(failed)
    assert report.execute_rehearsal_compare(str(baseline), str(failed)) == 1
    assert report.execute_rehearsal_compare(str(baseline), str(tmp_path / "x")) == 2


# --- Steps, query and command line ---------------------------------------------


def test_the_pause_and_resume_are_seeder_steps_without_a_due_instant():
    """pause and resume run like the other rehearsal steps but need no --due-at."""
    assert {"pause", "resume"} <= set(STEPS)
    assert not {"pause", "resume"} & DUE_STEPS


def test_the_credential_fingerprint_covers_every_credential_table():
    """One row per table; digests only, never a selected secret column alone."""
    for table in (
        "stewardship_family_campaign",
        "stewardship_family_code_mac",
        "stewardship_family_token",
        "stewardship_family_token_generation",
        "stewardship_rehearsal_credential",
    ):
        assert f"FROM {table}\n" in CREDENTIALS_SQL, table
    assert CREDENTIALS_SQL.count("md5(") == 5


@pytest.mark.parametrize(
    "argv",
    [
        ["local-send-probe", "--input", "x"],
        ["local-mail-audit", "--every", "5"],
        ["local-rehearsal-compare", "--input", "a"],
        ["local-rehearsal-compare", "--baseline", "a"],
    ],
)
def test_the_new_commands_take_only_their_options(argv, capsys):
    """Unsupported options, and a comparison without both inputs, are usage errors."""
    with pytest.raises(SystemExit) as raised:
        cli.main(argv)
    assert raised.value.code == 2


def test_a_reminder_prepared_ahead_passes_but_a_send_before_due_fails():
    """Messages created before the due time are prepare-ahead (BG-12 PR 2);
    a submission before the due time is still a fault."""
    document = measured(invariants={"messages before their occurrence": 4})
    correct = report.send_summary({"due_at": DUE.isoformat()}, document)["correctness"]
    assert correct["passed"] and correct["invariant_violations"] == {}
    early = measured()
    early["outcomes"][0]["submitted_at"] = (DUE - timedelta(seconds=5)).isoformat()
    correct = report.send_summary({"due_at": DUE.isoformat()}, early)["correctness"]
    assert correct["submitted_before_due"] == 1 and correct["passed"] is False
