"""A new campaign's first load refreshes the Family campaign statistics (#1932).

A first load bulk-inserts the campaign's Family rows and eligibility
history; with statistics that know only earlier campaigns the scheduler's
``FAMILY_INPUTS`` read re-aggregates the history once per Family. The
general worker now calls the worker-only definer
``stewardship_family_campaign_analyze_v1`` (0172) once that promotion
commits. These tests prove the real compiled handler does that once, as the
worker, outside any transaction, and not again on a later refresh; that the
definer refuses an unknown campaign and any login without its grant; that a
lock wait past its limit is logged and returns False; and that 0172's DO
block refuses a definer not installed as declared.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns import family_identity
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.deployment import ServiceRole

from .test_background_grants_postgresql import task_login
from .test_source_refreshing_postgresql import source_singletons  # noqa: F401
from .test_source_step_lock_postgresql import separate_session
from .test_source_unchanged_postgresql import Refreshes

pytestmark = pytest.mark.django_db(transaction=True)

FILE = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0172_family_campaign_statistics.sql"
)
DEFINER = "SELECT public.stewardship_family_campaign_analyze_v1(%s)"
TABLES = ("stewardship_family_campaign", "stewardship_family_eligibility")


def last_analyzed():
    """When each Family campaign table was last analyzed, read fresh."""
    with connection.cursor() as cursor:
        # Cumulative statistics are cached per transaction; read them anew.
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute(
            "SELECT pg_stat_get_last_analyze_time(%s::regclass), "
            "pg_stat_get_last_analyze_time(%s::regclass)",
            [f"public.{name}" for name in TABLES],
        )
        return cursor.fetchone()


def seen_campaign(families):
    """The one campaign the first load filled, with ``families`` rows."""
    (campaign,) = FamilyCampaign.objects.values_list(
        "campaign_id", flat=True
    ).distinct()
    assert FamilyCampaign.objects.filter(campaign_id=campaign).count() == families
    return campaign


def planned_families(campaign):
    """The planner's row estimate for one campaign's Family rows."""
    with connection.cursor() as cursor:
        cursor.execute(
            "EXPLAIN (FORMAT JSON) SELECT id FROM stewardship_family_campaign "
            "WHERE campaign_id = %s",
            [campaign],
        )
        return cursor.fetchone()[0][0]["Plan"]["Plan Rows"]


def test_a_first_load_analyzes_the_family_tables_once_it_commits(tmp_path, monkeypatch):
    """The real compiled handler, as the worker login, analyzes both tables
    once, after the first load's promotion commits; a later refresh of the
    same campaign does not analyze them again."""
    seen = []
    real = family_identity.analyze_family_campaign

    def analyze(campaign_id):
        """The real call, recording where it runs and what it changes."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT session_user")
            login = cursor.fetchone()[0]
        families = FamilyCampaign.objects.filter(campaign_id=campaign_id).count()
        before = last_analyzed()
        result = real(campaign_id)
        seen.append((connection.in_atomic_block, login, families, result, before))
        return result

    monkeypatch.setattr(family_identity, "analyze_family_campaign", analyze)
    refreshes = Refreshes(tmp_path, monkeypatch)
    ((atomic, login, families, result, before),) = seen
    assert atomic is False and login == "pk_stewardship_worker"
    assert families > 0 and result is True
    after = last_analyzed()
    for was, now in zip(before, after, strict=True):
        assert now is not None and (was is None or now > was)
    # The planner now knows the new campaign: its Family scan is estimated
    # at its real size, not the one row stale statistics give an unseen
    # campaign (the quadratic FAMILY_INPUTS plan).
    assert planned_families(seen_campaign(families)) == families
    # A later refresh of the same campaign adds no first load.
    refreshes.quick()
    assert len(seen) == 1


def test_the_definer_refuses_an_unknown_campaign_or_a_login_without_its_grant():
    """An unknown campaign is refused; PUBLIC and web cannot execute it."""
    with (
        pytest.raises(DatabaseError, match="takes Family effects"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(DEFINER, [uuid4()])
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege('public', "
            "'public.stewardship_family_campaign_analyze_v1(uuid)', 'EXECUTE')"
        )
        assert cursor.fetchone()[0] is False
    with (
        task_login(ServiceRole.WEB),
        pytest.raises(DatabaseError, match="permission denied"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(DEFINER, [uuid4()])


def test_a_refused_call_is_logged_and_returns_false(monkeypatch):
    """A refusal (here an unknown campaign) is a warning, never an error
    raised after the promotion has committed."""
    failures = []
    monkeypatch.setattr(
        family_identity,
        "emit_failure",
        lambda error, **options: failures.append((type(error), options)),
    )
    assert family_identity.analyze_family_campaign(uuid4()) is False
    ((kind, options),) = failures
    assert issubclass(kind, DatabaseError)
    assert options == {"level": 30}


def test_a_lock_wait_past_its_limit_is_logged_and_returns_false(tmp_path, monkeypatch):
    """Another session's ANALYZE-class lock on a Family table stops the call
    at its lock timeout: the timeout log records what, limit and elapsed,
    and the call returns False instead of raising."""
    from parishkit.stewardship.audit.models import OperationalLog

    seen = []
    real = family_identity.analyze_family_campaign
    monkeypatch.setattr(family_identity, "analyze_family_campaign", seen.append)
    Refreshes(tmp_path, monkeypatch)
    (campaign,) = seen
    monkeypatch.setattr(family_identity, "ANALYZE_LOCK_SECONDS", 1)
    blocker = separate_session()
    blocker.autocommit = False
    try:
        blocker.execute(
            "LOCK TABLE public.stewardship_family_eligibility "
            "IN SHARE UPDATE EXCLUSIVE MODE"
        )
        assert real(campaign) is False
    finally:
        blocker.rollback()
        blocker.close()
    entry = OperationalLog.objects.get(event="task_timed_out")
    assert entry.level == "WARNING"
    assert entry.context["what"] == "lock_timeout"
    assert entry.context["limit_seconds"] == 1
    assert 1 <= entry.context["elapsed_seconds"] < 10


def test_the_migration_check_refuses_a_definer_not_installed_as_declared():
    """0172's DO block raises when the definer has lost SECURITY DEFINER,
    PUBLIC may execute it, its owner cannot analyze the tables, or it is
    missing; all rolled back. The whole file installs on a database
    without it."""
    text = FILE.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    function = "public.stewardship_family_campaign_analyze_v1(uuid)"
    with connection.cursor() as cursor:
        cursor.execute(check)
        for statement in (
            f"ALTER FUNCTION {function} SECURITY INVOKER",
            f"GRANT EXECUTE ON FUNCTION {function} TO PUBLIC",
            # A role that owns none of the tables (created and rolled back).
            "CREATE ROLE pk_stewardship_analyze_probe NOLOGIN; "
            f"ALTER FUNCTION {function} OWNER TO pk_stewardship_analyze_probe",
            f"DROP FUNCTION {function}",
        ):
            with pytest.raises(DatabaseError, match="0172"), transaction.atomic():
                cursor.execute(statement)
                cursor.execute(check)
        with transaction.atomic():
            cursor.execute(f"DROP FUNCTION {function}")
            cursor.execute(text)
            transaction.set_rollback(True)
        cursor.execute(check)
