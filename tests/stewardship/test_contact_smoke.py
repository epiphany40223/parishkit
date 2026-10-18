"""The human-run Member contact smoke check, on a fake gateway only (#528).

No test here reaches ParishSoft: the gateway is an in-memory fake holding one
synthetic Member record, and the real gateway's probe is covered separately
in ``test_publication_worker``.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import parishkit
from parishkit import __version__
from parishkit.config import ConfigError
from parishkit.stewardship import provider_check_worker, smoke
from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses import contact_smoke
from parishkit.stewardship.responses.contact_schema import DATE_FIELDS, PUT_SCHEMA
from parishkit.stewardship.responses.contact_smoke import (
    MARKERS,
    RoundTripAborted,
    map_record,
    run,
)
from parishkit.stewardship.responses.publication import MEMBER_CONTACT_FIELDS
from parishkit.stewardship.responses.publication_gateway import (
    ContactGatewayError,
    ParishSoftContactGateway,
)
from parishkit.stewardship.runtime_paths import private_directory

from .test_smoke import consumer, outcome

FIXTURE = Path(__file__).parent / "fixtures/parishsoft_contact_capabilities.json"
SYNTHETIC = {
    "cellPhone": "(555) 555-0101",
    "dateOfBirth": "1970-01-02",
    "dateOfDeath": None,
    "emailAddress": "a@example.invalid; b@example.invalid",
    "fax": "(555) 555-0104",
    "firstName": "Synthetic",
    "gender": "F",
    "homePhone": "(555) 555-0102",
    "language": "English",
    "lastName": "Example",
    "maidenName": None,
    "middleName": None,
    "nickName": None,
    "pager": "(555) 555-0103",
    "workPhone": None,
}


class FakeGateway:
    """One synthetic Member record; writes apply under the record's spelling.

    A write is a partial update, as the live v2 contact PUT is (#528): fields
    left out are kept, ``null`` is ignored and ``""`` is refused
    (``write_rejected``) with nothing changed. Each accepted write bumps the
    volatile ``dateModified`` stamp. ``side``
    is a dict of extra record changes every write makes, ``failures`` a list
    of failure words (or exceptions) for successive writes, None for success.
    """

    def __init__(self, record, *, spelling=None, side=None, failures=()):
        """Hold the record, a PUT-name to record-key map and failure hooks."""
        self.record = dict(record)
        self.spelling = spelling or {}
        self.side = side or {}
        self.failures = list(failures)
        self.reads, self.writes = [], []

    def probe_member_record(self, member_id, *, timeout):
        """The stored record (a copy), as the real GET members/{id} answers."""
        self.reads.append(member_id)
        return dict(self.record)

    def write_member_contact(self, member_id, payload, *, timeout):
        """Apply one PUT, or fail as the next scripted failure says."""
        self.writes.append((member_id, dict(payload)))
        failure = self.failures.pop(0) if self.failures else None
        if isinstance(failure, BaseException):
            raise failure
        if failure is not None:
            raise ContactGatewayError(failure)
        if "" in payload.values():
            raise ContactGatewayError("write_rejected")
        for name, value in payload.items():
            if value is not None:
                self.record[self.spelling.get(name, name)] = value
        self.record.update(self.side)
        self.record["dateModified"] = len(self.writes)


def member(**changes):
    """The synthetic Member 42's record with ``changes``."""
    return {
        "memberDUID": 42,
        "familyDUID": 7,
        "dateModified": 0,
        "salutation": "Dear Synthetic",
        **SYNTHETIC,
        **changes,
    }


def recorded_run(gateway, **options):
    """Run with recorded sleeps and reports; returns (result, sleeps, reports)."""
    sleeps, reports = [], []
    result = run(
        gateway,
        42,
        sleep=sleeps.append,
        report=lambda value: reports.append(json.loads(json.dumps(value))),
        **options,
    )
    return result, sleeps, reports


def test_the_put_schema_is_the_recorded_one():
    """Lengths and date formats match the recorded request model exactly."""
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    schema = document["schemas"]["MemberContactUpdateRequestModel"]["properties"]
    assert set(PUT_SCHEMA) == set(schema) == set(MEMBER_CONTACT_FIELDS)
    for name, (minimum, maximum) in PUT_SCHEMA.items():
        item = schema[name]
        assert item["type"] == "string" and item["nullable"] is True
        assert (item.get("minLength", 0), item.get("maxLength")) == (minimum, maximum)
        assert (item.get("format") == "date") == (name in DATE_FIELDS)


def test_the_probe_reports_names_types_shapes_and_encodings_but_no_values():
    """Read only: no write, and the result carries no value, name or id."""
    gateway = FakeGateway(member())
    result, _, _ = recorded_run(gateway)
    assert gateway.writes == [] and gateway.reads == [42]
    assert result["read_complete"] is True and result["confirmed"] is False
    assert result["sent"] is False and result["read"] == "members/{id}"
    assert result["parishkit_version"] == __version__
    assert result["record_keys"] == sorted(member())
    assert result["unsendable"] == [] and result["markers_present"] == []
    assert result["unsettled"] == ["dateOfDeath", "workPhone"]
    fields = result["fields"]
    assert set(fields) == set(MEMBER_CONTACT_FIELDS)
    assert fields["gender"] == {
        "present": True,
        "source": "gender",
        "type": "string",
        "shape": "a",
        "encoder_matches_read": True,
        "sendable": True,
    }
    assert fields["dateOfBirth"]["shape"] == "9999-99-99"
    assert fields["cellPhone"]["shape"] == "(999) 999-9999"
    assert fields["emailAddress"]["addresses"] == 2
    assert fields["emailAddress"]["separators"] == ["; "]
    assert fields["pager"] == {
        "present": True,
        "source": "pager",
        "type": "string",
        "shape": "(999) 999-9999",
        "encoder_matches_read": None,
        "sendable": True,
    }
    assert fields["workPhone"] == {
        "present": True,
        "source": "workPhone",
        "type": "null",
        "sendable": True,
    }
    assert fields["language"]["encoder_matches_read"] is True
    text = json.dumps(result)
    for value in (v for v in SYNTHETIC.values() if v):
        assert value not in text
    assert "42" not in text and "Dear" not in text


def test_the_encoder_check_and_the_schema_check_name_their_fields():
    """A date-time, a long gender and a re-ordered email are reported.

    Only the marker field is ever sent, so other unsendable fields do not
    stop the round trip; an unsendable marker field does.
    """
    record = member(
        dateOfBirth="1970-01-02T00:00:00",
        gender="Female",
        emailAddress="B@example.invalid, a@example.invalid",
        homePhone="",
    )
    gateway = FakeGateway(record)
    result, _, _ = recorded_run(gateway, send=True)
    fields = result["fields"]
    assert fields["dateOfBirth"]["encoder_matches_read"] is False
    assert fields["emailAddress"]["encoder_matches_read"] is False
    assert fields["emailAddress"]["separators"] == [", "]
    assert fields["gender"]["sendable"] is False
    assert "encoder_matches_read" not in fields["homePhone"]
    assert result["unsendable"] == ["dateOfBirth", "gender", "homePhone"]
    assert "homePhone" in result["unsettled"]
    assert result["write"] == "accepted" and result["confirmed"] is True
    assert {name for _, body in gateway.writes for name in body} == {"pager"}
    gateway = FakeGateway(member(pager="555-0103"))
    result, _, _ = recorded_run(gateway, send=True)
    assert result["unsendable"] == ["pager"]
    assert result["write"] == "refused_unsendable" and gateway.writes == []


def test_shapes_mask_every_letter_and_digit_not_only_ascii():
    """Accented letters and non-ASCII digits are masked too."""
    assert contact_smoke._shape("Téléphone ٣-x") == "aaaaaaaaa 9-a"


def test_the_round_trip_writes_the_marker_then_restores_the_original():
    """Two one-field PUTs, as publication sends (#1600): marker, then original."""
    gateway = FakeGateway(member())
    result, sleeps, reports = recorded_run(gateway, send=True)
    assert gateway.writes == [
        (42, {"pager": MARKERS["pager"]}),
        (42, {"pager": SYNTHETIC["pager"]}),
    ]
    assert gateway.record == member(dateModified=2)
    assert sleeps == []
    # The marker step is reported before the restore runs.
    assert len(reports) == 1 and "restored" not in reports[0]
    assert reports[0]["marker_read_back"] is True
    assert {key: result[key] for key in result if key not in reports[0]} == {
        "restore_write": "accepted",
        "restore_attempts": 1,
        "restore_mismatch": [],
        "restored": True,
    }
    assert result["write"] == "accepted" and result["also_changed"] == []
    assert result["volatile_changed"] == ["dateModified"]
    assert result["confirmed"] is True


def test_an_unread_marker_field_refuses_any_write():
    """Without the marker field's original there is nothing to restore.

    Another unread field is only reported: no PUT sends it, so it cannot be
    cleared.
    """
    record = member()
    del record["fax"]
    gateway = FakeGateway(record)
    result, _, _ = recorded_run(gateway, send=True)
    assert result["read_complete"] is False and result["confirmed"] is True
    assert result["fields"]["fax"] == {"present": False, "ambiguous": False}
    gateway = FakeGateway(record)
    result, _, _ = recorded_run(gateway, send=True, field="fax")
    assert gateway.writes == [] and result["sent"] is False
    assert result["write"] == "refused_marker_field_unread"


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_blank_marker_field_refuses_any_write(blank):
    """The API cannot clear a value (null ignored, "" refused), so a blank
    marker field could never be put back: nothing is sent."""
    gateway = FakeGateway(member(pager=blank))
    result, _, _ = recorded_run(gateway, send=True)
    assert gateway.writes == [] and result["sent"] is False
    assert result["write"] == "refused_blank_marker_field"


def test_member_record_aliases_map_and_disagreeing_spellings_are_detailed():
    """``birthdate``/``sex``/``mobilePhone`` count; two differing values do not."""
    record = member()
    aliases = {
        "dateOfBirth": "birthdate",
        "gender": "sex",
        "cellPhone": "mobilePhone",
    }
    for name, alias in aliases.items():
        record[alias] = record.pop(name)
    payload, fields = map_record(record)
    assert set(payload) == set(MEMBER_CONTACT_FIELDS)
    assert fields["gender"]["source"] == "sex"
    # The same value under both spellings is still one clear answer.
    assert map_record({**record, "gender": "F"})[1]["gender"]["source"] == "gender"
    payload, fields = map_record({**record, "dateOfBirth": "1971-01-01T00:00:00"})
    assert "dateOfBirth" not in payload
    assert fields["dateOfBirth"] == {
        "present": False,
        "ambiguous": True,
        "sources": {
            "dateOfBirth": {"type": "string", "shape": "9999-99-99a99:99:99"},
            "birthdate": {"type": "string", "shape": "9999-99-99"},
        },
    }
    # A round trip through aliases writes the PUT names and restores them.
    gateway = FakeGateway(record, spelling=aliases)
    result, _, _ = recorded_run(gateway, send=True, field="fax")
    assert result["confirmed"] is True
    assert gateway.record == {**record, "dateModified": 2}
    assert "birthdate" not in gateway.writes[0][1]


@pytest.mark.parametrize(
    "record, message",
    [(None, "no Member"), (member(memberDUID=43), "another Member")],
)
def test_another_or_missing_member_is_refused_before_any_write(record, message):
    """The read must answer with the chosen Member's own id."""
    gateway = FakeGateway({})
    gateway.probe_member_record = lambda member_id, *, timeout: record
    with pytest.raises(ConfigError, match=message):
        run(gateway, 42, send=True)
    assert gateway.writes == []


def test_a_change_outside_the_request_model_fails_and_is_named():
    """A PUT that also rewrites another key is reported by name, never value."""
    gateway = FakeGateway(member(), side={"salutation": "PRIVATE-DERIVED"})
    result, sleeps, _ = recorded_run(gateway, send=True)
    assert result["also_changed"] == ["salutation"]
    assert result["restore_mismatch"] == ["salutation"]
    assert result["restored"] is False and result["confirmed"] is False
    # The restore was retried with backoff, then gave up.
    assert result["restore_attempts"] == 4 and sleeps == [5, 15, 30]
    assert "PRIVATE" not in json.dumps(result)


def test_a_normalized_marker_fails_confirmation():
    """A provider that rewrites the display-form phone does not confirm."""
    gateway = FakeGateway(member())
    original_write = gateway.write_member_contact

    def normalizing(member_id, payload, *, timeout):
        """Store the marker in canonical digits, as a normalizing API would."""
        original_write(member_id, payload, timeout=timeout)
        if gateway.record["pager"] == MARKERS["pager"]:
            gateway.record["pager"] = "5555550199"

    gateway.write_member_contact = normalizing
    result, _, _ = recorded_run(gateway, send=True)
    assert result["marker_read_back"] is False and result["confirmed"] is False
    assert result["restored"] is True


def test_a_failed_marker_write_settles_before_the_restore_read():
    """A timed-out PUT may land late: wait, then read; the restore still runs."""
    gateway = FakeGateway(member(), failures=["write_unavailable"])
    result, sleeps, _ = recorded_run(gateway, send=True)
    assert [payload for _, payload in gateway.writes] == [
        {"pager": MARKERS["pager"]},
        {"pager": SYNTHETIC["pager"]},
    ]
    assert result["write"] == "write_unavailable"
    assert result["restore_write"] == "accepted" and result["restored"] is True
    assert sleeps == [contact_smoke.SETTLE_SECONDS]
    assert result["confirmed"] is False


def test_a_failed_restore_is_retried_until_a_read_matches():
    """One failed restore PUT is followed by another after the first backoff."""
    gateway = FakeGateway(member(), failures=[None, "write_unavailable"])
    result, sleeps, _ = recorded_run(gateway, send=True)
    assert len(gateway.writes) == 3 and sleeps == [5]
    assert result["restore_attempts"] == 2 and result["restored"] is True
    assert result["confirmed"] is True


@pytest.mark.parametrize("error", [RuntimeError("PRIVATE"), KeyboardInterrupt()])
def test_an_unexpected_abort_still_restores_and_keeps_the_result(error):
    """A non-gateway error or Ctrl-C after the marker PUT: restore, then raise."""
    gateway = FakeGateway(member(), failures=[error])
    reports = []
    with pytest.raises(RoundTripAborted) as aborted:
        run(gateway, 42, send=True, sleep=lambda s: None, report=reports.append)
    partial = aborted.value.result
    assert partial["aborted"] is True and partial["restored"] is True
    assert len(gateway.writes) == 2 and reports
    assert "PRIVATE" not in json.dumps(partial)


def test_any_left_behind_marker_or_an_unlisted_field_is_refused():
    """A marker from an earlier run is reported and never overwritten."""
    gateway = FakeGateway(member(fax=MARKERS["fax"]))
    result, _, _ = recorded_run(gateway, send=True)
    assert result["markers_present"] == ["fax"]
    assert result["write"] == "refused_marker_present" and gateway.writes == []
    for field in ("nickName", "emailAddress"):
        with pytest.raises(ConfigError, match="smoke field"):
            run(gateway, 42, send=True, field=field)
    assert gateway.reads == [42]


def contact_args(**changes):
    """``check_parishsoft_contact`` options for the synthetic Member."""
    return {
        "organization_id": 7,
        "member_id": "42",
        "confirm_member_id": "42",
        **changes,
    }


@pytest.fixture
def no_database(monkeypatch):
    """The operator database setup, stubbed (pytest-django already set up)."""
    calls = []
    monkeypatch.setattr(
        "parishkit.stewardship.operator_commands.configure_operator_database",
        calls.append,
    )
    return calls


@pytest.mark.parametrize(
    "changes",
    [
        {"member_id": None, "confirm_member_id": None},
        {"confirm_member_id": None},
        {"confirm_member_id": "43"},
        {"member_id": "0", "confirm_member_id": "0"},
    ],
)
def test_the_command_refuses_without_an_explicit_confirmed_member(
    tmp_path, monkeypatch, no_database, changes
):
    """Nothing is read, not even the tenant check, without the id twice."""
    configuration = consumer(tmp_path, ServiceRole.WORKER, parishsoft=b"key")
    monkeypatch.setattr(
        smoke, "check_parishsoft", lambda *a, **k: pytest.fail("checked")
    )
    with pytest.raises((ConfigError, ValueError)):
        smoke.check_parishsoft_contact(configuration, **contact_args(**changes))


def test_the_command_never_runs_in_the_local_profile(
    tmp_path, monkeypatch, no_database
):
    """pk-local never writes to ParishSoft (#1399): refused before the
    database, the tenant read or the gateway."""
    from dataclasses import replace

    from parishkit.stewardship.deployment import DeploymentProfile

    configuration = replace(
        consumer(tmp_path, ServiceRole.WORKER, parishsoft=b"key"),
        profile=DeploymentProfile.LOCAL,
    )
    monkeypatch.setattr(
        smoke, "check_parishsoft", lambda *a, **k: pytest.fail("checked")
    )
    with pytest.raises(ConfigError, match="local profile"):
        smoke.check_parishsoft_contact(configuration, **contact_args(send=True))
    assert no_database == []


def test_the_command_runs_only_in_the_worker(tmp_path, no_database):
    """The worker holds the ParishSoft credential; nothing else may run it."""
    configuration = consumer(tmp_path, ServiceRole.WEB)
    with pytest.raises(ConfigError, match="worker"):
        smoke.check_parishsoft_contact(configuration, **contact_args())
    assert no_database == []


def test_the_command_checks_the_tenant_then_round_trips_the_one_member(
    tmp_path, monkeypatch, no_database, capsys
):
    """An invalid key stops before the Member; a valid one runs the check."""
    configuration = consumer(tmp_path, ServiceRole.WORKER, parishsoft=b"key")
    gateway = FakeGateway(member())
    gateway.close = lambda: setattr(gateway, "closed", True)
    opened = []
    monkeypatch.setattr(
        ParishSoftContactGateway,
        "open_operator",
        classmethod(lambda cls, credential, **kwargs: opened.append(kwargs) or gateway),
    )
    monkeypatch.setattr(provider_check_worker, "_parishsoft", outcome("invalid"))
    assert smoke.check_parishsoft_contact(configuration, **contact_args(send=True)) == {
        "credential": "invalid"
    }
    assert opened == [] and gateway.reads == []
    monkeypatch.setattr(provider_check_worker, "_parishsoft", outcome("valid"))
    result = smoke.check_parishsoft_contact(configuration, **contact_args(send=True))
    assert result["credential"] == "valid" and result["confirmed"] is True
    assert gateway.reads == [42, 42, 42] and {w[0] for w in gateway.writes} == {42}
    assert opened[0]["profile"] == configuration.profile.value
    assert opened[0]["on_timeout"] is smoke._contact_timeout
    assert gateway.closed is True
    # The marker step went to standard error before the restore.
    progress = json.loads(capsys.readouterr().err.strip())
    assert progress["step"] == "marker" and "restored" not in progress


def smoke_args(tmp_path, monkeypatch, **changes):
    """Console options for the contact target, over a rendered worker document."""
    from parishkit.stewardship.deployment_documents import deployment_document

    configuration = consumer(tmp_path, ServiceRole.WORKER, parishsoft=b"key")
    path = tmp_path / "worker.yaml"
    path.write_text(json.dumps(deployment_document(configuration)))
    monkeypatch.setenv("PARISHKIT_ROOT", str(tmp_path))
    monkeypatch.setattr(smoke, "configure_logging", lambda: None)
    values = dict(
        config=str(path),
        target="parishsoft_contact",
        organization_id="7",
        member_id="42",
        confirm_member_id="42",
        field=None,
        send=None,
    )
    return SimpleNamespace(**{**values, **changes})


def test_the_console_prints_the_one_line_and_the_partial_result_on_abort(
    tmp_path, monkeypatch, capsys
):
    """The contact branch's exact line; an abort prints the partial result."""
    args = smoke_args(tmp_path, monkeypatch, send=True)
    seen = []
    monkeypatch.setattr(
        smoke,
        "check_parishsoft_contact",
        lambda configuration, **options: seen.append(options) or {"confirmed": True},
    )
    assert smoke.execute_smoke(args) == 0
    assert capsys.readouterr().out == (
        '{"confirmed": true, "sent": false, "target": "parishsoft_contact"}\n'
    )
    assert seen == [
        {
            "organization_id": 7,
            "member_id": "42",
            "confirm_member_id": "42",
            "field": None,
            "send": True,
        }
    ]

    def aborting(configuration, **options):
        """Abort after a write, as an interrupted round trip does."""
        raise RoundTripAborted({"sent": True, "restored": True, "aborted": True})

    monkeypatch.setattr(smoke, "check_parishsoft_contact", aborting)
    assert smoke.execute_smoke(args) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "target": "parishsoft_contact",
        "sent": True,
        "restored": True,
        "aborted": True,
    }
    assert "smoke check refused" in captured.err
    monkeypatch.setattr(
        smoke,
        "check_parishsoft_contact",
        lambda configuration, **options: (_ for _ in ()).throw(ConfigError("PRIVATE")),
    )
    assert smoke.execute_smoke(args) == 2
    captured = capsys.readouterr()
    assert not captured.out and "PRIVATE" not in captured.err


def test_a_killed_request_is_recorded_with_no_task(monkeypatch):
    """What, limit and elapsed are logged; no task owns a human's run."""
    from parishkit.stewardship.audit import timeouts

    calls = []
    monkeypatch.setattr(
        timeouts, "record_timeout", lambda event, **facts: calls.append((event, facts))
    )
    smoke._contact_timeout(30, 31.5)
    assert calls == [
        (
            smoke.Event.HELPER_TIMED_OUT,
            {
                "what": "source_helper",
                "helper": "parishsoft_http_worker",
                "limit_seconds": 30,
                "elapsed_seconds": 31.5,
                "bind_task": False,
            },
        )
    ]


SUBPROCESS = """
import sys
from pathlib import Path

from django.conf import settings

from parishkit.stewardship import operator_commands, provider_check_worker, smoke
from parishkit.stewardship.deployment import load_deployment

assert not settings.configured
provider_check_worker._parishsoft = lambda value, context, session: True
real = operator_commands.configure_operator_database


def configured_then_fenced(configuration):
    real(configuration)
    from parishkit.stewardship.responses import publication_gateway

    def reached(cls, credential, **options):
        print("REACHED_OPENER")
        raise SystemExit(0)

    publication_gateway.ParishSoftContactGateway.open_operator = classmethod(reached)


operator_commands.configure_operator_database = configured_then_fenced
smoke.check_parishsoft_contact(
    load_deployment(Path(sys.argv[1])),
    organization_id=7,
    member_id="42",
    confirm_member_id="42",
)
raise SystemExit(3)
"""


def test_the_command_imports_the_gateway_only_after_configuring_django(tmp_path):
    """In a fresh process with no Django settings it reaches the opener."""
    from parishkit.stewardship.deployment_documents import deployment_document

    configuration = consumer(tmp_path, ServiceRole.WORKER, parishsoft=b"key")
    # Django settings name the worker's database login; nothing connects.
    password = configuration.postgres.password_file
    password.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    private_directory(password.parent, create=True)
    write_private(password, b"synthetic-password\n")
    document = tmp_path / "worker.yaml"
    document.write_text(json.dumps(deployment_document(configuration)))
    script = tmp_path / "fresh_process.py"
    script.write_text(SUBPROCESS, encoding="utf-8")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != "DJANGO_SETTINGS_MODULE"
    }
    environment["PARISHKIT_ROOT"] = str(tmp_path)
    # The child imports the same parishkit this test did, wherever it lives.
    package_root = str(Path(parishkit.__file__).resolve().parents[1])
    environment["PYTHONPATH"] = os.pathsep.join(
        [package_root, *filter(None, [environment.get("PYTHONPATH")])]
    )
    completed = subprocess.run(
        [sys.executable, str(script), str(document)],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.strip() == "REACHED_OPENER"


def test_the_parser_admits_the_member_options_for_smoke(monkeypatch):
    """--member-id, --confirm-member-id and --field reach the smoke command."""
    from parishkit.stewardship.cli import main

    called = []
    monkeypatch.setattr(smoke, "execute_smoke", lambda args: called.append(args) or 0)
    argv = ["smoke", "--config", "c.yaml", "--target", "parishsoft_contact"]
    argv += ["--organization-id", "7", "--member-id", "42"]
    argv += ["--confirm-member-id", "42", "--field", "fax", "--send"]
    assert main(argv) == 0
    args = called[0]
    assert (args.member_id, args.confirm_member_id, args.field) == ("42", "42", "fax")
    assert args.send is True


def test_ctrl_c_is_ignored_from_the_marker_put_until_the_restore_ends():
    """No interrupt can fall between the marker, its report and the restore."""
    import signal

    gateway = FakeGateway(member())
    seen = []
    original_write = gateway.write_member_contact

    def watched(member_id, payload, *, timeout):
        """Record the SIGINT handler in force during each PUT."""
        seen.append(signal.getsignal(signal.SIGINT))
        original_write(member_id, payload, timeout=timeout)

    gateway.write_member_contact = watched
    before = signal.getsignal(signal.SIGINT)
    result = run(
        gateway,
        42,
        send=True,
        sleep=lambda seconds: None,
        report=lambda value: seen.append(signal.getsignal(signal.SIGINT)),
    )
    assert seen == [signal.SIG_IGN] * 3 and result["confirmed"] is True
    assert signal.getsignal(signal.SIGINT) is before


def test_a_helper_that_will_not_drain_still_gets_the_restore():
    """A stranded helper is a failed write: settle, then restore and verify."""
    from parishkit.parishsoft_transport import SourceTransportDrainFailure

    gateway = FakeGateway(member(), failures=[SourceTransportDrainFailure("x")])
    result, sleeps, _ = recorded_run(gateway, send=True)
    assert result["write"] == "drain_failed" and "aborted" not in result
    assert len(gateway.writes) == 2 and result["restore_write"] == "accepted"
    assert result["restore_attempts"] == 1 and result["restored"] is True
    assert sleeps == [contact_smoke.SETTLE_SECONDS]
    assert result["confirmed"] is False
    # A restore PUT that will not drain is retried after the backoff.
    gateway = FakeGateway(member(), failures=[None, SourceTransportDrainFailure("x")])
    result, sleeps, _ = recorded_run(gateway, send=True)
    assert len(gateway.writes) == 3 and sleeps == [5]
    assert result["restore_attempts"] == 2 and result["restored"] is True
