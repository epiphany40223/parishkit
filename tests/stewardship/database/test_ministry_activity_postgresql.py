"""The retired local Ministry activity in PostgreSQL (#1267, migration 0091).

The table and its projection are gone; a new configuration version holding a
``ministries`` record is refused by Python and again by SQL, applied history
that recorded one still verifies, and 0091's guard and closing check refuse
what they should, including each older function body the frozen files hold.
"""

import re
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
)
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.configuration_snapshots import (
    is_prepared,
    prepare_snapshot,
)

from ..configuration_factory import configuration_version
from ..test_ministry_activity import activity, policy_document
from .campaign_builders import initialized

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
FROZEN = (MIGRATIONS / "0091_drop_ministry_activity.sql").read_text(encoding="utf-8")
GUARD = FROZEN[FROZEN.index("DO $guard$") : FROZEN.index("$guard$;") + len("$guard$;")]
CHECK = FROZEN[FROZEN.index("DO $check$") :]
# The grants the check compares with, recorded before anything changes.
_ACL = FROZEN.index("SELECT set_config('stewardship.migration_0091_acl'")
ACL = FROZEN[_ACL : FROZEN.index(", true);", _ACL) + len(", true);")]
# The newest older body of each replaced function that a frozen file holds:
# each still reads the dropped table.
OLDER = {
    "stewardship_campaign_pointer_v1": "0042_ministry_leaders_from_roles.sql",
    "stewardship_chair_decisions_v1": "0026_chair_decisions_plan.sql",
    "stewardship_ministry_report_v1": "0045_report_duid_sorts.sql",
    "stewardship_leaders_without_user_v1": "0057_admin_session_key_digests.sql",
    "stewardship_ministry_report_v3": "0064_report_local_days.sql",
    "stewardship_ministry_leaders_at_v1": "0070_ministry_leader_changes.sql",
    "stewardship_ministry_complete_v1": "0075_campaign_series.sql",
}


def prepare(version):
    """Exercise complete durable preparation, not test-transaction savepoints."""
    return prepare_snapshot(version, actor_id=uuid4(), correlation_id=uuid4())


def older_body(name):
    """``name``'s older ``CREATE OR REPLACE`` statement from its frozen file."""
    text = (MIGRATIONS / OLDER[name]).read_text(encoding="utf-8")
    match = re.search(rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{name}\(", text, re.M)
    quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, match.start())
    end = text.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1
    statement = text[match.start() : end]
    assert "stewardship_ministry_activity" in statement, name
    return statement.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def refused(*statements):
    """Run ``statements`` between 0091's grant record and its check, rolled
    back; return the check's error."""
    with (
        pytest.raises(DatabaseError) as error,
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL check_function_bodies = false")
        cursor.execute(ACL)
        for statement in statements:
            cursor.execute(statement)
        cursor.execute(CHECK)
    return str(error.value)


def test_the_installed_schema_passes_the_check():
    """A fresh install (baseline, then every frozen file) is what 0091 declares."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(ACL)
        cursor.execute(CHECK)
        cursor.execute("SELECT to_regclass('public.stewardship_ministry_activity')")
        assert cursor.fetchone() == (None,)
        transaction.set_rollback(True)


@pytest.mark.parametrize("name", sorted(OLDER))
def test_the_check_refuses_each_older_body(name):
    """An older body that still reads the dropped table fails the migration."""
    assert "still reads Ministry activity" in refused(older_body(name))


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE public.stewardship_ministry_activity (id uuid)",
        "CREATE FUNCTION public.stewardship_ministry_projection_v1() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
        "CREATE FUNCTION public.stewardship_ministry_activity_immutable_v1() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
    ],
)
def test_the_check_refuses_what_it_dropped(statement):
    """The table or either trigger function, back again, fails the migration."""
    assert "table or its functions remain" in refused(statement)


def test_the_check_refuses_a_definer_that_lost_its_rights():
    """The Users page's leaders list must stay SECURITY DEFINER."""
    message = refused(
        "ALTER FUNCTION public.stewardship_leaders_without_user_v1(text, uuid) "
        "SECURITY INVOKER"
    )
    assert "not installed as declared" in message


@pytest.mark.parametrize("active", [False, True])
def test_the_guard_refuses_an_inactive_ministry_in_the_active_configuration(
    tmp_path, active
):
    """Upgrading over an inactive Ministry would silently show it again."""
    store, root, actor = initialized(tmp_path)
    with transaction.atomic(), connection.cursor() as cursor:
        # The table as the release before 0091 had it, reduced to what the
        # guard reads, with one row for the active configuration.
        cursor.execute(
            "CREATE TABLE public.stewardship_ministry_activity "
            "(configuration_id uuid, active boolean)"
        )
        cursor.execute(
            "INSERT INTO public.stewardship_ministry_activity "
            "SELECT active_configuration_id, %s "
            "FROM public.stewardship_system_configuration",
            [active],
        )
        if active:
            cursor.execute(GUARD)
        else:
            with (
                pytest.raises(DatabaseError, match="marks a Ministry inactive"),
                transaction.atomic(),
            ):
                cursor.execute(GUARD)
        transaction.set_rollback(True)


def test_a_request_adding_a_ministries_record_is_refused(tmp_path):
    """No request records a change to the retired section."""
    store, root, actor = initialized(tmp_path)
    with pytest.raises(ConfigError, match="Ministry activity section"):
        record_request(
            base_digest=root.digest,
            patch=[{"operation": "add", "section": "ministries", **activity()}],
            actor_id=actor,
            request_key=uuid4(),
            correlation_id=uuid4(),
        )


def no_python_refusal(monkeypatch):
    """Let a ``ministries`` record past Python, as a bypassed check would."""
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.ministry_activity.trusts_all", lambda: True
    )


def test_sql_refuses_a_new_version_with_ministries_records(monkeypatch):
    """Without Python's refusal, the completeness trigger still refuses."""
    no_python_refusal(monkeypatch)
    with pytest.raises(IntegrityError, match="ministries section must be empty"):
        prepare(configuration_version(policy_document(activity())))
    assert not AppliedConfigurationVersion.objects.exists()


def test_applied_history_with_ministries_records_still_verifies(monkeypatch):
    """A version applied before 0091 kept its section, and still verifies.

    The completeness trigger is held off only to store such a version, as
    the release before 0091 could; verification then reads the section
    from the document, as there is no projection left.
    """
    with monkeypatch.context() as patched:
        no_python_refusal(patched)
        version = configuration_version(policy_document(activity()))
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_configuration_version "
                "DISABLE TRIGGER stewardship_ministry_complete_v1"
            )
            try:
                prepare(version)
            finally:
                cursor.execute(
                    "ALTER TABLE stewardship_configuration_version "
                    "ENABLE TRIGGER stewardship_ministry_complete_v1"
                )
    assert is_prepared(version.digest)
    stored = AppliedConfigurationVersion.objects.get(pk=version.version_id)
    assert stored.validation_schema == "ministry-activity-v4"


def test_the_check_refuses_changed_grants():
    """A replaced function must keep the grants it had before."""
    message = refused(
        "GRANT EXECUTE ON FUNCTION public.stewardship_chair_decisions_v1(uuid) "
        "TO PUBLIC"
    )
    assert "grants changed" in message
