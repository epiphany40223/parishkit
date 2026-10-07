"""Restore review guards that need no database (#537, migration 0019).

The hard rule: a restore never creates, replaces or cancels a Family code or
link, so neither the frozen SQL nor the Python owner may write any Family
credential table. The operator command takes only its own options and reads
the backup time without guessing a time zone. The database behavior is
proven in database/test_restore_review_postgresql.py.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import cli, operator_commands

PACKAGE = Path(cli.__file__).parent
FROZEN = PACKAGE / "schema" / "migrations" / "0019_restore_review.sql"
OWNER = PACKAGE / "campaigns" / "restore_review.py"

# Every table that holds or binds a Family code or link.
CREDENTIAL_TABLES = (
    "stewardship_family_token",
    "stewardship_family_token_generation",
    "stewardship_family_code_mac",
    "stewardship_credential_deployment",
    "stewardship_campaign_credentials",
    "stewardship_rehearsal_credential",
    "stewardship_rehearsal_code_mac",
    "stewardship_family_session",
)


def test_restore_review_never_writes_a_family_credential():
    """No INSERT, UPDATE or DELETE of a Family credential table, in SQL or Python."""
    sql = FROZEN.read_text(encoding="utf-8")
    for table in CREDENTIAL_TABLES:
        pattern = rf"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(public\.)?{table}\b"
        assert not re.search(pattern, sql, re.I), table
    source = OWNER.read_text(encoding="utf-8")
    for name in ("FamilyAccessToken", "CampaignCredentialState", "FamilySession"):
        assert name not in source
    # The epoch and token pointers are never named, so never rotated.
    assert "family_link_epoch" not in sql and "active_token_generation" not in sql


def test_release_and_decisions_record_the_fresh_sign_in():
    """Both web steps pass the session and its sign-in for the SQL check."""
    source = OWNER.read_text(encoding="utf-8")
    for function in ("settle_held_email", "release_review"):
        body = source.split(f"def {function}(")[1].split("\ndef ")[0]
        assert "session_id=session_id" in body
        assert "authenticated_at=authenticated_at" in body
    sql = FROZEN.read_text(encoding="utf-8")
    assert (
        sql.count(
            "stewardship_restore_decision_admitted_v1(NEW.actor_id,NEW.session_id,"
            "NEW.authenticated_at)"
        )
        >= 2
    )


def test_restore_begin_accepts_only_its_options(monkeypatch):
    """--config, --backup-at and --reason; it runs as an operator command."""
    calls = []
    monkeypatch.setattr(
        "parishkit.stewardship.operator_commands.execute_operator",
        lambda args: calls.append(args) or 0,
    )
    arguments = ["restore-begin", "--config", "x", "--backup-at", "20541005T020000Z"]
    assert cli.main(arguments) == 0
    assert calls[0].backup_at == "20541005T020000Z" and calls[0].reason is None
    with pytest.raises(SystemExit):
        cli.main([*arguments, "--email", "a"])


def test_the_backup_time_is_a_set_name_or_an_instant_with_its_offset():
    """A set's UTC name or ISO 8601 with an offset; a bare local time is refused."""
    instant = datetime(2054, 10, 5, 2, tzinfo=UTC)
    assert operator_commands.parse_backup_time("20541005T020000Z") == instant
    assert operator_commands.parse_backup_time("2054-10-05T02:00:00Z") == instant
    assert operator_commands.parse_backup_time("2054-10-04T22:00:00-04:00") == instant
    for value in (None, "2054-10-05T02:00:00", "yesterday", "20541005T020000"):
        with pytest.raises(ConfigError):
            operator_commands.parse_backup_time(value)


def test_restore_begin_refuses_a_bad_time_before_any_admission():
    """The time is checked before the profile or the database is touched."""
    with pytest.raises(ConfigError):
        operator_commands.restore_begin_command(object(), backup_at="soon", reason="")
