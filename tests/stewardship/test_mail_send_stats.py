"""Send statistics (#284): closed values, per-phase timings, no personal data."""

import json
import os
import re
import smtplib
import time
from contextlib import nullcontext

import pytest

from parishkit.stewardship import family_delivery_process
from parishkit.stewardship.family_delivery import (
    MAX_STAT_NUMBER,
    MAX_STATS,
    STAT_WORDS,
    FamilyDeliveryResult,
    send_stats,
)
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs import family_mail_results
from parishkit.stewardship.jobs.family_mail_dispatch import shared_fault
from parishkit.stewardship.jobs.family_mail_results import result_evidence

from .family_mail_session_fakes import LIMIT, SETTINGS, FakeGmailHelpers, mail
from .send_stats_cases import ADMITTED, REFUSED
from .test_family_mail_session import KEY, Server, send, smtp_session


@pytest.mark.parametrize("value", ADMITTED)
def test_send_stats_admit_numbers_booleans_ids_and_closed_words(value):
    """What the SQL validator admits, the Python validator admits too."""
    assert send_stats(value) == value


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_send_stats_refuse_anything_that_could_hold_personal_data(case):
    """Addresses, host names, prose, open words and structure are refused."""
    with pytest.raises(ValueError):
        send_stats(REFUSED[case])
    with pytest.raises(ValueError):
        send_stats([("a", 1)])


def test_the_word_list_matches_the_sql_validator():
    """STAT_WORDS and the SQL VALUES list are the same closed set."""
    from pathlib import Path

    sql = (
        Path(family_delivery_process.__file__).parent / "schema" / "delivery.sql"
    ).read_text()
    block = sql[sql.index("OR (stat.key,stat.value#>>'{}') IN (VALUES") :]
    block = block[: block.index("THEN RETURN NULL")]
    pairs = set(re.findall(r"\('([a-z_]+)','([a-z_]+)'\)", block))
    assert pairs == {(key, word) for key, words in STAT_WORDS.items() for word in words}
    assert "count(*) FROM jsonb_object_keys(result->'stats'))>48" in sql
    assert MAX_STATS == 48 and len(str(MAX_STAT_NUMBER)) == 12


def test_invalid_statistics_never_reject_an_accepted_result(caplog):
    """A bad statistic is dropped with a warning; the acceptance stands."""
    wire = FamilyDeliveryResult(Status.ACCEPTED, 2).wire_payload() | {
        "stats": {"who": "a@example.org"}
    }
    result = FamilyDeliveryResult.from_payload(wire, recipient_count=2, wire=True)
    assert result.status is Status.ACCEPTED and result.stats is None
    for stats in ([1], "x", {"nested": {"a": 1}}):
        wire["stats"] = stats
        again = FamilyDeliveryResult.from_payload(wire, recipient_count=2, wire=True)
        assert again.status is Status.ACCEPTED and again.stats is None
    direct = FamilyDeliveryResult(Status.ACCEPTED, 2, stats={"x_ms": -1})
    assert direct.stats is None
    assert any("dropped" in record.getMessage() for record in caplog.records)


def test_a_session_helper_with_bad_statistics_still_reports_acceptance():
    """The parent keeps a sent message sent whatever its statistics hold."""

    class Helper:
        def __init__(self):
            self.read, stdin_fd = os.pipe()
            stdout_fd, self.write = os.pipe()
            self.stdin = os.fdopen(stdin_fd, "wb")
            self.stdout = os.fdopen(stdout_fd, "rb")
            self.returncode = None
            result = FamilyDeliveryResult(Status.ACCEPTED, 2).wire_payload()
            result["stats"] = {"who": "a@example.org", "big": 10**20}
            os.write(self.write, b"started 1\n")
            os.write(
                self.write, json.dumps({"seq": 1, "result": result}).encode() + b"\n"
            )

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9
            os.close(self.write)
            os.close(self.read)

        def wait(self, timeout=None):
            # Quit on the parent's EOF, as the real helper does, so close()
            # reaps it instead of leaving it for a later reaper kill (#542),
            # and close this fake's own pipe ends as an exiting helper would.
            if self.returncode is None:
                self.returncode = 0
                os.close(self.write)
                os.close(self.read)
            return self.returncode

    session = family_delivery_process.FamilyMailSession(spawn=Helper)
    result = send(session, mail())
    assert result.status is Status.ACCEPTED and result.stats is None
    session.close()


def test_stats_cross_the_pipe_but_never_change_the_outcome():
    """Equal outcomes compare equal; the wire line carries the statistics."""
    plain = FamilyDeliveryResult(Status.ACCEPTED, 2)
    timed = FamilyDeliveryResult(Status.ACCEPTED, 2, stats={"smtp_ms": 5})
    assert plain == timed and "stats" not in timed.payload()
    wire = timed.wire_payload()
    assert wire["stats"] == {"smtp_ms": 5}
    back = FamilyDeliveryResult.from_payload(wire, recipient_count=2, wire=True)
    assert back.stats == {"smtp_ms": 5}
    with pytest.raises(ValueError):
        FamilyDeliveryResult.from_payload(wire, recipient_count=2)


def no_personal_data(stats, *addresses):
    """Statistics never contain an address or anything address-like."""
    text = json.dumps(stats)
    assert "@" not in text and not any(item in text for item in addresses)
    return send_stats(stats)


def test_a_fresh_then_reused_connection_is_described(monkeypatch):
    """Phase times and connection use, message by message."""
    server = Server()
    session = smtp_session(monkeypatch, server)
    first = session.deliver(mail()).stats
    second = session.deliver(mail()).stats
    no_personal_data(first, "a@example.org", SETTINGS["sender"])
    assert first["conn_reused"] is False and first["token_refreshed"] is True
    assert (first["conn_seq"], first["conn_index"]) == (1, 1)
    assert second["conn_reused"] is True and second["token_refreshed"] is False
    assert (second["conn_seq"], second["conn_index"]) == (1, 2)
    assert "conn_age_ms" in second and "conn_age_ms" not in first
    for phase in ("token_ms", "connect_ms", "auth_ms"):
        assert type(first[phase]) is int and first[phase] >= 0
    for stats in (first, second):
        for phase in ("envelope_ms", "data_ms", "smtp_ms"):
            assert type(stats[phase]) is int and stats[phase] >= 0
        assert (stats["cap_conn_msgs"], stats["cap_conn_s"]) == (50, 300)
    # A reused connection did no token, connect or AUTH work, so it records
    # none: setup percentiles then describe only real setups.
    assert not {"token_ms", "connect_ms", "auth_ms"} & set(second)


def test_replacements_and_ends_are_named(monkeypatch):
    """stale, cap_messages, non_accepted and connect_failed are recorded."""
    server = Server()
    session = smtp_session(monkeypatch, server)
    session.deliver(mail())
    server.faults = {(1, "mail"): smtplib.SMTPServerDisconnected("idle")}
    stale = session.deliver(mail()).stats
    assert stale["conn_replaced"] == "stale" and stale["conn_reused"] is False
    assert stale["conn_seq"] == 2
    server.faults = {(2, "rcpt"): (550, b"5.1.1 No such user")}
    refused = session.deliver(mail("a@example.org")).stats
    assert refused["conn_end"] == "non_accepted"
    monkeypatch.setattr("parishkit.stewardship.family_delivery.CONNECTION_MESSAGES", 1)
    session.deliver(mail())
    capped = session.deliver(mail()).stats
    assert capped["conn_replaced"] == "cap_messages"
    session.drop()
    server.faults = {(5, "ehlo"): OSError("refused")}
    failed = session.deliver(mail()).stats
    assert failed["conn_end"] == "connect_failed"
    # The connection it tried to open, never an index on an earlier one.
    assert failed["conn_seq"] == 5 and "conn_index" not in failed
    assert failed["connect_ms"] >= 0 and "auth_ms" not in failed


def test_a_stale_retry_describes_the_new_connection(monkeypatch):
    """The retry's connection is new: no inherited age, number or index."""
    server = Server()
    session = smtp_session(monkeypatch, server)
    session.deliver(mail())
    session.deliver(mail())
    server.faults = {(1, "mail"): smtplib.SMTPServerDisconnected("idle")}
    stale = session.deliver(mail()).stats
    assert stale["conn_replaced"] == "stale" and "conn_age_ms" not in stale
    assert (stale["conn_seq"], stale["conn_index"]) == (2, 1)
    # A failed retry connect names the new connection, with no index.
    session.deliver(mail())
    server.faults = {
        (2, "mail"): smtplib.SMTPServerDisconnected("idle"),
        (3, "ehlo"): OSError("refused"),
    }
    failed = session.deliver(mail()).stats
    assert failed["conn_seq"] == 3 and "conn_index" not in failed
    assert failed["conn_end"] == "connect_failed" and "conn_age_ms" not in failed


def test_a_token_failure_points_at_no_connection(monkeypatch):
    """No connection was opened, so none is named: token_failed says why."""
    from google.auth.exceptions import RefreshError

    server = Server()
    session = smtp_session(monkeypatch, server)
    session.deliver(mail())
    session.drop()

    def refused(*args):
        raise RefreshError("private", retryable=True)

    session.credentials = None
    monkeypatch.setattr("parishkit.stewardship.family_delivery._credentials", refused)
    stats = session.deliver(mail()).stats
    assert stats["conn_end"] == "token_failed" and stats["token_refreshed"] is True
    assert not {"conn_seq", "conn_index", "connect_ms", "auth_ms"} & set(stats)
    assert stats["token_ms"] >= 0


def test_a_limit_refusal_is_named_in_the_statistics(monkeypatch):
    """The limit kind is kept beside the evidence, never inside it."""
    server = Server({(1, "mail"): (550, b"5.4.5 Daily user sending limit exceeded.")})
    session = smtp_session(monkeypatch, server)
    result = session.deliver(mail())
    assert result.limit == "daily" and result.stats["limit"] == "daily"
    assert "limit" not in result.payload()


def test_the_batched_session_adds_helper_statistics(tmp_path, monkeypatch):
    """Helper numbering, spawn time and why each helper ended."""
    gmail = FakeGmailHelpers(tmp_path / "gmail")
    session = gmail.session()
    first = send(session, mail())
    one = session.take_stats()
    send(session, mail())
    two = session.take_stats()
    assert first.stats["smtp_ms"] >= 0 and "helper_id" not in first.stats
    assert (one["helper_seq"], one["helper_index"]) == (1, 1)
    assert (two["helper_seq"], two["helper_index"]) == (1, 2)
    assert one["helper_id"] == two["helper_id"] and one["spawn_ms"] >= 0
    # Only the message that started the helper records spawn time.
    assert "spawn_ms" not in two and one["transport"] == "batched"
    assert one["cap_helper_msgs"] == family_delivery_process.SESSION_MESSAGES
    gmail.script(("mail", LIMIT))
    send(session, mail())
    limited = session.take_stats()
    assert limited["helper_end"] == "limit"
    send(session, mail())
    after = session.take_stats()
    assert after["helper_seq"] == 2 and "prev_helper_end" not in after
    send(session, mail(), settings=SETTINGS | {"sender_name": "Parish Office"})
    changed = session.take_stats()
    assert changed["prev_helper_end"] == "key_change"
    gmail.script(("request", ["exit"]))
    send(session, mail(), settings=SETTINGS | {"sender_name": "Parish Office"})
    restarted = session.take_stats()
    assert restarted["helper_restart"] is True and "helper_end" not in restarted
    assert session.take_stats() == {}
    for stats in (one, two, limited, after, changed, restarted):
        no_personal_data(stats, "a@example.org")
    session.close()


def test_evidence_keeps_health_first_and_stats_optional(monkeypatch):
    """The '{"health":...' prefix readers still match a note with statistics."""
    monkeypatch.setattr(family_mail_results, "stats_admitted", lambda: True)
    result = FamilyDeliveryResult(
        Status.UNAVAILABLE, 1, stats={"smtp_ms": 3, "transport": "batched"}
    )
    evidence = result_evidence(result, semantic_key=KEY)
    note = evidence.evidence_note
    assert note.startswith('{"health":"unavailable",') and '"stats":{' in note
    assert shared_fault(evidence.reason, note)
    plain = result_evidence(
        FamilyDeliveryResult(Status.UNAVAILABLE, 1), semantic_key=KEY
    )
    assert '"stats"' not in plain.evidence_note


def test_statistics_never_block_a_settlement(monkeypatch):
    """Too long for the column, or not yet admitted: the note drops them."""
    # 48 valid entries of about 60 characters each exceed varchar(2000).
    stats = {f"k{index:02d}_{'x' * 34}": 10**15 for index in range(48)}
    huge = FamilyDeliveryResult(Status.ACCEPTED, 1, stats=stats)
    monkeypatch.setattr(family_mail_results, "stats_admitted", lambda: True)
    note = result_evidence(huge, semantic_key=KEY).evidence_note
    assert len(json.dumps(stats)) > 2000
    assert len(note) <= 2000 and '"stats"' not in note
    small = FamilyDeliveryResult(Status.ACCEPTED, 1, stats={"smtp_ms": 1})
    assert '"stats"' in result_evidence(small, semantic_key=KEY).evidence_note
    monkeypatch.setattr(family_mail_results, "stats_admitted", lambda: False)
    assert '"stats"' not in result_evidence(small, semantic_key=KEY).evidence_note


def test_admission_is_rechecked_until_the_database_admits_it(monkeypatch, caplog):
    """Only this release's exact validator body admits statistics.

    One WARNING while it differs, a recheck after the interval, then kept.
    """
    expected = family_mail_results.expected_validator_md5()
    assert re.fullmatch(r"[0-9a-f]{32}", expected)
    answers = [("0" * 32,), (expected,)]
    queries = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            queries.append(params)

        def fetchone(self):
            return answers[len(queries) - 1]

    now = [100.0]
    monkeypatch.setattr(family_mail_results.connection, "cursor", Cursor)
    monkeypatch.setattr(family_mail_results.transaction, "atomic", nullcontext)
    monkeypatch.setattr(family_mail_results.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        family_mail_results,
        "_admitted",
        {"value": None, "checked": 0.0, "refused": False},
    )
    assert family_mail_results.stats_admitted() is False
    assert family_mail_results.stats_admitted() is False and len(queries) == 1
    now[0] += family_mail_results.STATS_RECHECK_SECONDS
    assert family_mail_results.stats_admitted() is True
    assert family_mail_results.stats_admitted() is True and len(queries) == 2
    warnings = [r for r in caplog.records if "not recorded" in r.getMessage()]
    assert len(warnings) == 1
    assert queries[0] == [family_mail_results.VALIDATOR]


def test_the_expected_md5_is_the_in_place_sql_body():
    """The runtime check and the in-place SQL's check name the same body."""
    from pathlib import Path

    schema = (
        Path(family_mail_results.__file__).parents[1] / "schema" / "delivery.sql"
    ).read_text()
    start = schema.index(
        "CREATE FUNCTION public.stewardship_family_smtp_result_v1(event uuid)"
    )
    body = schema[schema.index("AS $$", start) + 5 : schema.index("$$;", start)]
    import hashlib

    assert hashlib.md5(body.encode()).hexdigest() == (
        family_mail_results.expected_validator_md5()
    )
    assert "result ? 'stats'" in body


def test_a_failed_admission_check_counts_as_not_admitted(monkeypatch):
    """The check never raises into the settlement that asked."""

    def broken():
        raise RuntimeError("synthetic catalog failure")

    monkeypatch.setattr(family_mail_results.connection, "cursor", broken)
    monkeypatch.setattr(family_mail_results.transaction, "atomic", nullcontext)
    monkeypatch.setattr(
        family_mail_results,
        "_admitted",
        {"value": None, "checked": 0.0, "refused": False},
    )
    assert family_mail_results.stats_admitted() is False


class Refusal(Exception):
    """A psycopg-like cause carrying an SQLSTATE."""

    def __init__(self, sqlstate):
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def refused(sqlstate):
    """A Django DatabaseError caused by ``sqlstate``, as the driver raises it."""
    from django.db import IntegrityError

    error = IntegrityError("Family provider result is invalid")
    error.__cause__ = Refusal(sqlstate)
    return error


def test_refused_statistics_are_retried_without_them(monkeypatch, caplog):
    """A 23514 refusal of a note with stats settles the same outcome without."""
    monkeypatch.setattr(family_mail_results, "stats_admitted", lambda: True)
    monkeypatch.setattr(family_mail_results.transaction, "atomic", nullcontext)
    monkeypatch.setattr(
        family_mail_results,
        "_admitted",
        {"value": True, "checked": 0.0, "refused": False},
    )
    result = FamilyDeliveryResult(Status.ACCEPTED, 1, stats={"smtp_ms": 3})
    notes = []

    def settle(evidence):
        notes.append(evidence.evidence_note)
        if '"stats"' in evidence.evidence_note:
            raise refused("23514")
        return "settled"

    settle_with_stats = family_mail_results.settle_with_stats
    assert settle_with_stats(settle, result, semantic_key=KEY) == "settled"
    assert '"stats"' in notes[0] and '"stats"' not in notes[1]
    assert family_mail_results._admitted["value"] is False
    # A second refusal does not warn again.
    family_mail_results._admitted["value"] = True
    settle_with_stats(settle, result, semantic_key=KEY)
    warnings = [r for r in caplog.records if "refused" in r.getMessage()]
    assert len(warnings) == 1


def test_other_failures_and_plain_notes_are_not_retried(monkeypatch):
    """Only a validator refusal of statistics earns the retry."""
    monkeypatch.setattr(family_mail_results, "stats_admitted", lambda: True)
    monkeypatch.setattr(family_mail_results.transaction, "atomic", nullcontext)
    calls = []

    def settle(evidence):
        calls.append(evidence.evidence_note)
        raise refused("40001")

    timed = FamilyDeliveryResult(Status.ACCEPTED, 1, stats={"smtp_ms": 3})
    with pytest.raises(Exception, match="Family provider result is invalid"):
        family_mail_results.settle_with_stats(settle, timed, semantic_key=KEY)
    assert len(calls) == 1
    calls.clear()

    def invalid(evidence):
        calls.append(evidence.evidence_note)
        raise refused("23514")

    plain = FamilyDeliveryResult(Status.ACCEPTED, 1)
    with pytest.raises(Exception, match="Family provider result is invalid"):
        family_mail_results.settle_with_stats(invalid, plain, semantic_key=KEY)
    assert len(calls) == 1


def test_elapsed_ms_is_whole_and_never_negative():
    """Milliseconds are integers; a clock step backwards reads as zero."""
    from parishkit.stewardship.family_delivery import elapsed_ms

    started = time.monotonic()
    assert type(elapsed_ms(started)) is int and elapsed_ms(started) >= 0
    assert elapsed_ms(started, started - 5) == 0
