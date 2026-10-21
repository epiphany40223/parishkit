"""Accepting a large ParishSoft change once, under the real guard (#530).

The web login records an acceptance only for a signed-in Administrator's
live session (any sign-in age, #1547), only for the newest refusal with no
full refresh promoted since, only with the counts exactly as recorded, and
never for an empty load; nobody changes or deletes one. Whether an
acceptance is live is derived in SQL: it ends at 24 hours, at the next
refusal and at the next promoted full refresh. The worker reads the live
one, a full refresh within the reviewed counts goes through under it and
records it, and the refresh after that compares with the accepted counts
(#831's required test).
"""

import logging
import re
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.admin_session_store import SessionStore
from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.observability import Event
from parishkit.stewardship.source import transport
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.count_names import DERIVED_COUNTS
from parishkit.stewardship.source.drop_acceptance import (
    ACCEPTANCE_KEY,
    AcceptanceLapsed,
    acceptor,
    live_acceptance,
    reviewed_counts,
)
from parishkit.stewardship.source.drop_models import (
    SourceDropAcceptance,
    SourceDropCount,
)
from parishkit.stewardship.source.execution import _promote
from parishkit.stewardship.source.failures import settle_failed_read
from parishkit.stewardship.source.leases import release_source
from parishkit.stewardship.source.loading import CountCheck, DestructiveSourceChange
from parishkit.stewardship.source.models import (
    SourceCurrent,
    SourceMutationLease,
    SourceSnapshot,
)
from parishkit.stewardship.source.refreshing import (
    TREND_DAYS,
    _largest_counts,
    _trend,
)

from .auth_builders import unguarded
from .test_background_grants_postgresql import task_login
from .test_portal_identity_guards_postgresql import forge
from .test_source_attempts_postgresql import setup
from .test_source_refreshing_postgresql import (
    fake_provider,
    next_full,
    pages,
    run,
    seed_full,
)

pytestmark = pytest.mark.django_db(transaction=True)

WEB = "pk_stewardship_web"
CHECK_SQL = "SELECT public.stewardship_source_drop_acceptance_active_v1()"


@pytest.fixture(autouse=True)
def source_singletons():
    """Disposable flush recreates only idle source migration seeds."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def live():
    """The live acceptance's id, as the SQL lifetime check answers it."""
    with connection.cursor() as cursor:
        cursor.execute(CHECK_SQL)
        return cursor.fetchone()[0]


def refuse(execution, lease, credential, checks):
    """Begin one attempt on ``lease`` and settle it as a refusal with ``checks``."""
    attempt = begin_refresh_attempt(execution, lease, credential)
    failed = next((each for each in checks if each.failed), checks[0])
    settle_failed_read(
        execution,
        DestructiveSourceChange(
            "PRIVATE",
            measure=failed.measure,
            before=failed.before,
            after=failed.after,
            checks=checks,
        ),
        source_claim=lease,
    )
    return attempt


FELL = (
    CountCheck("family", 100, 60, 25, True),
    CountCheck("member", 200, 190, 25, False),
)


@contextmanager
def web_with(*privileges):
    """The web login with ``privileges`` on the acceptance table added.

    Web holds SELECT and INSERT since System health's button (ADM-13 PR 5b);
    granting them again changes nothing, and granting UPDATE or DELETE here
    shows the guard itself refuses both, whatever the grants. Django reads
    both times back, so an insert needs SELECT too.
    """
    with task_login(ServiceRole.WEB, exact=True):
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            for privilege in privileges:
                cursor.execute(
                    f"GRANT {privilege} ON stewardship_source_drop_acceptance TO {WEB}"
                )
            cursor.execute(f"SET SESSION AUTHORIZATION {WEB}")
        yield


def administrator():
    """A just-signed-in Administrator's live session.

    The source tests' configuration (``setup``) has one Administrator address
    rule; its user and a session that signed in just now are written as the
    schema owner, as the portal identity guard tests do.
    """
    user = PortalUser.objects.create(
        google_subject="synthetic-admin-subject",
        email="admin@example.org",
        verified_at=database_now(),
    )
    return forge(user.pk)


def session():
    """The signed-in Administrator's live session."""
    return PortalSession.objects.order_by("-authenticated_at").first()


def web_accept(attempt, current=None, **values):
    """Accept ``attempt``'s refusal as the web would, as recorded.

    ``current`` is the Administrator's session, read beforehand when the
    login in use cannot read sessions.
    """
    current = current or session()
    row = {
        "id": uuid4(),
        "actor_id": current.principal_id,
        "session_id": current.pk,
        "authenticated_at": current.authenticated_at,
        "attempt_id": attempt.pk,
        "counts": reviewed_counts(SourceDropCount.objects.filter(attempt=attempt)),
    } | values
    with transaction.atomic():
        return SourceDropAcceptance.objects.create(**row)


def owner_accept(attempt, **values):
    """An acceptance written directly as the schema owner (no checks)."""
    row = {
        "actor_id": uuid4(),
        "session_id": uuid4(),
        "authenticated_at": database_now(),
        "attempt_id": attempt.pk,
        "counts": reviewed_counts(SourceDropCount.objects.filter(attempt=attempt)),
    } | values
    return SourceDropAcceptance.objects.create(**row)


def change(statement):
    """Run one UPDATE or DELETE on the acceptances in its own transaction.

    The model refuses both in Python, so the guard is reached with SQL.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(statement)


def database_now():
    """The database clock."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        return cursor.fetchone()[0]


@contextmanager
def refused(message):
    """A change refused with ``message`` and rolled back."""
    with pytest.raises(DatabaseError, match=message), transaction.atomic():
        yield


def test_the_web_accepts_the_newest_refusal_once_for_an_administrator(tmp_path):
    """The session's own sign-in, the counts as recorded, database times."""
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = refuse(execution, lease, credential, FELL)
    administrator()
    current = session()
    with web_with("SELECT", "INSERT"):
        # A sign-in instant not the session's, another Administrator's id or
        # a made-up session is refused.
        for values in (
            {"authenticated_at": current.authenticated_at.replace(year=2000)},
            {"actor_id": uuid4()},
            {"session_id": uuid4()},
        ):
            with refused("requires a live Administrator session"):
                web_accept(attempt, **values)
        # The counts must be exactly the recorded ones.
        reviewed = reviewed_counts(FELL)
        reviewed["family"]["after"] = 61
        with refused("differ from the recorded refusal"):
            web_accept(attempt, counts=reviewed)
        with refused("differ from the recorded refusal"):
            web_accept(attempt, counts={"family": reviewed["family"]})
        accepted = web_accept(attempt, created_at=database_now() - timedelta(days=9))
        # One live acceptance at a time.
        with refused("still in effect"):
            web_accept(attempt)
    assert accepted.expires_at - accepted.created_at == timedelta(hours=24)
    assert abs(database_now() - accepted.created_at) < timedelta(minutes=1)
    assert accepted.counts == reviewed_counts(FELL)
    assert live() == accepted.pk


def test_no_other_login_accepts_and_nobody_changes_or_deletes(tmp_path):
    """Even with every grant added, the guard keeps the boundary."""
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = refuse(execution, lease, credential, FELL)
    current = administrator()
    with task_login(ServiceRole.WORKER, exact=True), refused("permission denied"):
        web_accept(attempt, current)
    with task_login(ServiceRole.WORKER, exact=True):
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(
                "GRANT INSERT ON stewardship_source_drop_acceptance "
                "TO pk_stewardship_worker"
            )
            cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_worker")
        with refused("Only the web portal"):
            web_accept(attempt, current)
    owner_accept(attempt)
    longer = (
        "UPDATE stewardship_source_drop_acceptance "
        "SET expires_at=expires_at+interval '9 days'"
    )
    remove = "DELETE FROM stewardship_source_drop_acceptance"
    with web_with("SELECT", "INSERT", "UPDATE", "DELETE"):
        for statement in (longer, remove):
            with refused("never changed or deleted"):
                change(statement)
    # Not even the schema owner.
    for statement in (longer, remove):
        with refused("never changed or deleted"):
            change(statement)
    assert SourceDropAcceptance.objects.count() == 1


@pytest.mark.parametrize(
    "checks,message",
    [
        # Every count passed (only the empty check refused it, say).
        ((CountCheck("family", 100, 90, 25, False),), "no count that fell"),
        # No Members at all: refused at any setting, never accepted.
        (
            (
                CountCheck("family", 100, 60, 25, True),
                CountCheck("member", 200, 0, 25, True),
            ),
            "no Families or no Members",
        ),
    ],
)
def test_a_refusal_with_nothing_to_accept_is_refused(tmp_path, checks, message):
    """Only a refusal for a count that fell, with Families and Members."""
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = refuse(execution, lease, credential, checks)
    administrator()
    with web_with("SELECT", "INSERT"), refused(message):
        web_accept(attempt)


def test_only_the_newest_refusal_with_no_full_promoted_since_is_accepted(tmp_path):
    """A later refusal or a promoted full refresh settles the older one."""
    credential, execution, lease, *_ = setup(tmp_path)
    seed_full(credential, execution, lease)
    execution, lease = next_full()
    older = refuse(execution, lease, credential, FELL)
    execution, lease = next_full()
    newer = refuse(execution, lease, credential, FELL)
    administrator()
    with web_with("SELECT", "INSERT"), refused("no longer the newest"):
        web_accept(older)
    execution, lease = next_full()
    unrefused = seed_full(credential, execution, lease).refresh_attempt
    with web_with("SELECT", "INSERT"):
        with refused("no longer the newest"):
            web_accept(newer)
        # An attempt with no recorded counts is no refusal at all.
        with refused("names a refused"):
            web_accept(unrefused, counts={})


def test_one_acceptance_per_refusal_even_after_it_lapses(tmp_path):
    """A lapsed acceptance is not renewed; a fresh refusal is accepted instead."""
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = refuse(execution, lease, credential, FELL)
    owner_accept(attempt, expires_at=database_now() - timedelta(seconds=1))
    assert live() is None
    administrator()
    with web_with("SELECT", "INSERT"), refused("attempt_id"):
        web_accept(attempt)


def test_an_acceptance_ends_at_expiry_a_new_refusal_or_a_promoted_full(tmp_path):
    """The lifetime is derived from immutable facts: nothing ends it by hand."""
    credential, execution, lease, *_ = setup(tmp_path)
    seed_full(credential, execution, lease)
    execution, lease = next_full()
    first = refuse(execution, lease, credential, FELL)
    expired = owner_accept(first, expires_at=database_now() - timedelta(seconds=1))
    assert live() is None
    assert expired.pk
    execution, lease = next_full()
    second = refuse(execution, lease, credential, FELL)
    accepted = owner_accept(second, expires_at=database_now() + timedelta(hours=24))
    assert live() == accepted.pk
    # A new refusal ends it.
    execution, lease = next_full()
    third = refuse(execution, lease, credential, FELL)
    assert live() is None
    accepted = owner_accept(third, expires_at=database_now() + timedelta(hours=24))
    assert live() == accepted.pk
    # A promoted full refresh ends it, whether or not it used it.
    execution, lease = next_full()
    seed_full(credential, execution, lease)
    assert live() is None


def test_the_worker_reads_the_live_acceptance_and_writes_none(tmp_path):
    """Its SELECT grant covers the read; its guard admits no worker write."""
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = refuse(execution, lease, credential, FELL)
    accepted = owner_accept(attempt, expires_at=database_now() + timedelta(hours=24))
    with task_login(ServiceRole.WORKER, exact=True):
        found = live_acceptance()
        with refused("permission denied"):
            change("UPDATE stewardship_source_drop_acceptance SET counts='{}'")
    assert (found.id, found.actor_id, found.counts) == (
        accepted.pk,
        accepted.actor_id,
        reviewed_counts(FELL),
    )
    assert found.organization_id == attempt.request.organization_id
    with task_login(ServiceRole.SCHEDULER, exact=True), refused("permission denied"):
        live_acceptance()


def no_safety_window(monkeypatch):
    """The faked calls reserve no HTTP safety window, so the next full refresh
    can take the source at once; safety windows are tested elsewhere."""
    monkeypatch.setattr(transport, "reserve_source_request", lambda *a, **k: None)


def run_full(credential, monkeypatch, **changes):
    """One real full refresh of the one-Family fixture through the pipeline.

    ``changes`` are ``pages()`` options, such as a Member's changed fields.
    """
    execution, lease = next_full()
    fake_provider(monkeypatch, pages(**changes))
    try:
        return execution, lease, run(credential, execution, lease)
    except DestructiveSourceChange as error:
        settle_failed_read(execution, error, source_claim=lease)
        raise


def promote(execution, lease, snapshot):
    """Promote a ready full refresh through the worker's own promotion step
    (with its acceptance check) and finish its task."""
    _promote(execution, lease, snapshot, lambda *args: True)
    with execution.effect():
        release_source(lease)
    execution.transition("complete")


def accept_newest():
    """Accept the newest refusal as a freshly signed-in Administrator."""
    newest = SourceDropCount.objects.order_by("-created_at").first().attempt
    administrator()
    with web_with("SELECT", "INSERT"):
        return web_accept(newest)


def failed_measures(attempt):
    """The measures that failed in one refusal, as a manifest records them."""
    return sorted(
        SourceDropCount.objects.filter(attempt=attempt, failed=True).values_list(
            "measure", flat=True
        )
    )


def test_an_accepted_change_promotes_once_and_starts_the_trend_again(
    tmp_path, monkeypatch, caplog
):
    """#831's required test: refusal, acceptance, the accepted full promotes;
    the next full with the same counts is not refused, and its trend ends at
    the accepted full. The acceptance ends with that promotion."""
    no_safety_window(monkeypatch)
    credential, execution, lease, *_ = setup(tmp_path)
    # Two Families promoted; the provider now has one: 50% fell.
    before = seed_full(credential, execution, lease)
    assert before.counts["family"] == 2
    with pytest.raises(DestructiveSourceChange):
        run_full(credential, monkeypatch)
    refusal = SourceDropCount.objects.get(measure="family")
    assert (refusal.before, refusal.after, refusal.failed) == (2, 1, True)
    # With no acceptance it is refused again (a new refusal).
    with pytest.raises(DestructiveSourceChange):
        run_full(credential, monkeypatch)
    accepted = accept_newest()
    with caplog.at_level(logging.INFO, logger="parishkit.stewardship"):
        execution, lease, result = run_full(credential, monkeypatch)
    assert result.state == "ready" and result.counts["family"] == 1
    load = result.cursor["load"]
    assert load[ACCEPTANCE_KEY] == {
        "id": str(accepted.pk),
        "actor_id": str(accepted.actor_id),
        "failed": failed_measures(accepted.attempt),
    }
    assert "family" in load[ACCEPTANCE_KEY]["failed"]
    assert load["maximum_drop_percent"] == 25
    assert [
        record.extra["source_drop_acceptance"]
        for record in caplog.records
        if record.msg is Event.TASK_STARTED
        and record.extra.get("source_drop_acceptance")
    ] == [accepted.pk]
    # Until something ends it, the acceptance is still live.
    assert live() == accepted.pk
    promote(execution, lease, result)
    assert live() is None
    # The next full, with the same accepted counts, is not refused: the
    # Family count's trend starts at the accepted full, though the week's
    # trend still holds the full before it for every other count.
    execution, lease, following = run_full(credential, monkeypatch)
    assert following.state == "ready"
    assert ACCEPTANCE_KEY not in following.cursor["load"]
    promote(execution, lease, following)
    newest_full = SourceSnapshot.objects.get(pk=following.pk)
    trend = _trend(newest_full, database_now() - timedelta(days=TREND_DAYS))
    assert [each.pk for each in trend] == [following.pk, result.pk, before.pk]
    assert _largest_counts(trend)["family"] == 1
    # Using it again: a further drop over 25% (the Member's email gone, so
    # the email counts fall to zero) is refused at the normal limit.
    with pytest.raises(DestructiveSourceChange) as refused:
        run_full(credential, monkeypatch, member_change={"emailAddress": None})
    assert refused.value.loss[0] in DERIVED_COUNTS
    assert SourceCurrent.objects.get().snapshot_id == following.pk


def test_a_count_that_did_not_fail_in_the_refusal_is_not_accepted(
    tmp_path, monkeypatch
):
    """A load under a live acceptance in which a count fails that passed in
    the refusal stays refused, and that new refusal ends the acceptance."""
    no_safety_window(monkeypatch)
    credential, execution, lease, *_ = setup(tmp_path)
    seed_full(credential, execution, lease)
    with pytest.raises(DestructiveSourceChange):
        run_full(credential, monkeypatch)
    accepted = accept_newest()
    newly = set(DERIVED_COUNTS) - set(failed_measures(accepted.attempt))
    with pytest.raises(DestructiveSourceChange):
        run_full(credential, monkeypatch, member_change={"emailAddress": None})
    newest = SourceDropCount.objects.order_by("-created_at").first().attempt
    assert set(failed_measures(newest)) & newly
    assert live() is None


def test_an_acceptance_that_lapses_before_promotion_refuses_it(
    tmp_path, monkeypatch, caplog
):
    """The read and the promotion are one decision: an acceptance that
    expires between the load and the promotion stops the promotion, and the
    refresh is refused with nothing new recorded."""
    no_safety_window(monkeypatch)
    credential, execution, lease, *_ = setup(tmp_path)
    before = seed_full(credential, execution, lease)
    with pytest.raises(DestructiveSourceChange):
        run_full(credential, monkeypatch)
    accepted = accept_newest()
    refusals = SourceDropCount.objects.count()
    execution, lease, result = run_full(credential, monkeypatch)
    assert result.cursor["load"][ACCEPTANCE_KEY]["id"] == str(accepted.pk)
    # The schema owner, with triggers off, ages the acceptance past 24 hours.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "UPDATE stewardship_source_drop_acceptance "
            "SET expires_at=statement_timestamp()-interval '1 second'"
        )
    with pytest.raises(AcceptanceLapsed) as lapsed:
        promote(execution, lease, result)
    assert lapsed.value.acceptance == accepted.pk
    with caplog.at_level(logging.INFO, logger="parishkit.stewardship"):
        settle_failed_read(execution, lapsed.value, source_claim=lease)
    # The process log names the lapsed acceptance as the reason.
    assert [
        record.extra["source_acceptance_lapsed"]
        for record in caplog.records
        if record.msg is Event.SOURCE_DESTRUCTIVE_CHANGE
        and record.levelno == logging.CRITICAL
        and record.extra.get("source_acceptance_lapsed")
    ] == [accepted.pk]
    assert SourceCurrent.objects.get().snapshot_id == before.pk
    assert SourceSnapshot.objects.get(pk=result.pk).state == "rejected"
    assert SourceDropCount.objects.count() == refusals


def test_an_acceptance_for_another_organization_is_not_honored(tmp_path):
    """The worker honors only a refusal of the organization it is loading."""
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = refuse(execution, lease, credential, FELL)
    accepted = owner_accept(attempt, expires_at=database_now() + timedelta(hours=24))
    organization = attempt.request.organization_id
    task = uuid4()
    assert acceptor(organization + 1, task)(FELL) is None
    assert acceptor(organization, task)(FELL) == {
        "id": str(accepted.pk),
        "actor_id": str(accepted.actor_id),
        "failed": ["family"],
    }


def test_a_load_beyond_the_reviewed_change_stays_refused(tmp_path, monkeypatch):
    """Counts below what the Administrator reviewed: refused, and that new
    refusal ends the acceptance."""
    no_safety_window(monkeypatch)
    credential, execution, lease, *_ = setup(tmp_path)
    seed_full(credential, execution, lease)
    with pytest.raises(DestructiveSourceChange):
        run_full(credential, monkeypatch)
    attempt = SourceDropCount.objects.get(measure="family").attempt
    reviewed = reviewed_counts(SourceDropCount.objects.filter(attempt=attempt))
    # As though the Administrator had reviewed a smaller drop than this load's.
    reviewed["family"]["after"] = 2
    accepted = owner_accept(
        attempt, counts=reviewed, expires_at=database_now() + timedelta(hours=24)
    )
    assert live() == accepted.pk
    with pytest.raises(DestructiveSourceChange):
        run_full(credential, monkeypatch)
    assert live() is None
    assert SourceCurrent.objects.get().snapshot.counts["family"] == 2


MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0080_drop_acceptance.sql"
).read_text(encoding="utf-8")
CHECK = re.search(r"^DO \$check\$.*?^\$check\$;", MIGRATION, re.M | re.S)[0]


GUARD = MIGRATION[
    MIGRATION.index(
        "CREATE FUNCTION public.stewardship_source_drop_acceptance_guard_v1()"
    ) : MIGRATION.index("END $$;") + len("END $$;")
].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


@pytest.mark.parametrize(
    "old,new",
    [
        ("'pk_stewardship_web'", "'pk_stewardship_worker'"),
        ("c.measure IN ('family','member') AND c.after=0", "false"),
        ("instant+interval '24 hours'", "instant+interval '48 hours'"),
    ],
)
def test_the_migration_check_refuses_a_weakened_guard(old, new):
    """The web-login, empty-load and 24-hour checks are pinned in the guard."""
    assert GUARD.count(old) == 1
    with (
        connection.cursor() as cursor,
        pytest.raises(DatabaseError, match="guard is not the one"),
        transaction.atomic(),
    ):
        cursor.execute(GUARD.replace(old, new))
        cursor.execute(CHECK)


def test_the_migration_check_passes_and_refuses_drift():
    """The closing DO block passes with 0080's guard and refuses each drift.

    0114 (#1547) later replaces the guard, so each rolled-back transaction
    first puts 0080's own body back.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(GUARD)
        cursor.execute(CHECK)
        transaction.set_rollback(True)
    table = "public.stewardship_source_drop_acceptance"
    for change_sql, message in (
        (f"DROP TABLE {table} CASCADE", "not installed"),
        (
            f"ALTER TABLE {table} DROP CONSTRAINT "
            "stewardship_source_drop_acceptance_attempt_id_key",
            "keys were not created",
        ),
        (
            f"ALTER TABLE {table} DROP CONSTRAINT "
            "stewardship_source_d_attempt_id_6c026bc5_fk_stewardsh",
            "keys were not created",
        ),
        ("DROP INDEX public.source_drop_acceptance_newest", "index was not created"),
        (
            "ALTER FUNCTION public.stewardship_source_drop_acceptance_guard_v1() "
            "SET search_path = public",
            "guard is not the one",
        ),
        (
            "ALTER FUNCTION public.stewardship_source_drop_acceptance_active_v1() "
            "SET search_path = public",
            "lifetime is not installed",
        ),
        (
            f"DROP TRIGGER stewardship_source_drop_acceptance_guard ON {table}; "
            "CREATE TRIGGER stewardship_source_drop_acceptance_guard BEFORE INSERT "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION "
            "public.stewardship_source_drop_acceptance_guard_v1()",
            "trigger was not created",
        ),
        (f"GRANT SELECT ON {table} TO PUBLIC", "PUBLIC holds a privilege"),
        (
            "ALTER FUNCTION public.stewardship_source_drop_acceptance_guard_v1() "
            "SECURITY DEFINER",
            "guard is not the one",
        ),
        (
            "GRANT EXECUTE ON FUNCTION "
            "public.stewardship_source_drop_acceptance_guard_v1() TO PUBLIC",
            "guard is not the one",
        ),
        (
            f"DROP TRIGGER stewardship_source_drop_acceptance_guard ON {table}",
            "trigger was not created",
        ),
        (
            "ALTER FUNCTION public.stewardship_source_drop_acceptance_active_v1() "
            "SECURITY DEFINER",
            "lifetime is not installed",
        ),
        (
            "ALTER FUNCTION public.stewardship_source_drop_acceptance_active_v1() "
            "VOLATILE",
            "lifetime is not installed",
        ),
    ):
        with (
            connection.cursor() as cursor,
            pytest.raises(DatabaseError, match=message),
            transaction.atomic(),
        ):
            cursor.execute(GUARD)
            cursor.execute(change_sql)
            cursor.execute(CHECK)


def unguarded_session(user, *, authenticated_at):
    """A live Admin session row for ``user``, written past the session guard
    (as only a superuser could) so a test controls who it names and when it
    signed in."""

    store = SessionStore()
    store.save()
    now = database_now()
    with unguarded():
        return PortalSession.objects.create(
            session_id=store.stored_key,
            principal_id=user.pk,
            authenticated_at=authenticated_at,
            last_activity_at=now,
            expires_at=now + timedelta(hours=1),
        )


def test_the_guard_refuses_repeatable_read_and_non_administrators(tmp_path):
    """READ COMMITTED only; a disabled user or one who is no Administrator is
    refused even with a live session."""
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = refuse(execution, lease, credential, FELL)
    current = administrator()
    with web_with("SELECT", "INSERT"), refused("requires READ COMMITTED"):
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        web_accept(attempt, current)
    stranger = PortalUser.objects.create(
        google_subject="synthetic-stranger-subject",
        email="stranger@example.org",
        verified_at=database_now(),
    )
    outsider = unguarded_session(stranger, authenticated_at=database_now())

    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_portal_user SET disabled=true WHERE id=%s",
            [current.principal_id],
        )
    with web_with("SELECT", "INSERT"):
        for session_row in (current, outsider):
            with refused("requires a live Administrator session"):
                web_accept(attempt, session_row)
    assert not SourceDropAcceptance.objects.exists()


def test_a_live_session_of_any_sign_in_age_is_admitted(tmp_path):
    """A live session signed in three hours ago is admitted (#1547); the same
    kind of session idle for over an hour, expired or revoked is refused."""
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = refuse(execution, lease, credential, FELL)
    user = PortalUser.objects.get(pk=administrator().principal_id)
    hours_ago = database_now() - timedelta(hours=3)
    for kind in ("idle", "expired", "revoked"):
        login = unguarded_session(user, authenticated_at=hours_ago)
        # Read after the row is written: its checks keep activity before
        # expiry and revocation.
        now = database_now()
        values = {
            "idle": {"last_activity_at": now - timedelta(minutes=61)},
            "expired": {
                "last_activity_at": now - timedelta(minutes=1),
                "expires_at": now,
            },
            "revoked": {"revoked_at": now},
        }[kind]
        with unguarded():
            PortalSession.objects.filter(pk=login.pk).update(**values)
        with (
            web_with("SELECT", "INSERT"),
            refused("requires a live Administrator session"),
        ):
            web_accept(attempt, login)
    login = unguarded_session(user, authenticated_at=hours_ago)
    with web_with("SELECT", "INSERT"):
        accepted = web_accept(attempt, login)
    assert accepted.session_id == login.pk
    assert accepted.authenticated_at == hours_ago
