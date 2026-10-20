"""An Administrator reviews Automatic census changes under the real web role (#528).

Migration 0096 (``schema/migrations/0096_proposal_review.sql``) adds the
review history and lets the web login apply exactly what a review row
records. These tests run the service and the SQL guards as installed, the
worklist's Review over HTTP, and the file's own DO block against the
derived guard's previous (0090) body.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.census_review import review_census_change
from parishkit.stewardship.responses.models import ProposalReview, ProposedChange
from parishkit.stewardship.storage import StaleRecordError

from .test_background_grants_postgresql import task_login
from .test_census_resolution_postgresql import leader, prepared
from .test_publication_plans_postgresql import owner, ready  # noqa: F401
from .test_publication_worker_postgresql import (  # noqa: F401 (fixture)
    confirmed,
    results,
    run,
)

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
FROZEN = MIGRATIONS / "migrations/0096_proposal_review.sql"
ROUTE = "/admin/reports/census/"
# A row's Back to To review button (the page help and the bulk bar name it
# too, so its label alone does not tell them apart).
BACK = b"resolve=to_review&amp;"


def review(harness, actor, proposal, decision, *, version=None, key=None, note=""):
    """Review through the service on the restricted web login."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return review_census_change(
            harness.service.store,
            actor,
            proposal.pk,
            expected_version=proposal.version if version is None else version,
            request_key=key or uuid4(),
            decision=decision,
            note=note,
        )


def fresh(row):
    """The proposal as stored now."""
    return ProposedChange.objects.get(pk=row.pk)


def test_approve_ignore_and_back_to_review_keep_history_and_audit(response_service):
    """Each review moves only the decision, advances the version, keeps who,
    when and the note, and is audited with versions only; a repeated request
    is one row and a rebound request key is refused."""
    harness, admin, _, _, name = prepared(response_service)
    key = uuid4()
    first = review(harness, admin, name, "approved", key=key, note="Checked")
    again = review(harness, admin, name, "approved", key=key, note="Checked")
    assert again.pk == first.pk and ProposalReview.objects.count() == 1
    with pytest.raises(ValueError):
        review(harness, admin, name, "ignored", key=key, note="Checked")
    name = fresh(name)
    assert (name.decision, name.execution) == ("approved", "pending")
    assert name.version == first.expected_version + 1
    assert first.actor_id == admin and first.note == "Checked" and first.created_at
    assert (first.admin_value_set, first.admin_value) == (False, None)
    event = AuditEvent.objects.get(event_type="census_change_updated")
    assert event.subject_id == first.pk
    assert AuditContext.objects.get(event=event).context == {
        "outcome": "changed",
        "before_version": first.expected_version,
        "after_version": first.expected_version + 1,
    }
    review(harness, admin, name, "ignored")
    # Sending an ignored change back to review says why (#1683).
    with pytest.raises(ValueError):
        review(harness, admin, fresh(name), "unreviewed", note=" ")
    review(harness, admin, fresh(name), "unreviewed", note="Asked again")
    name = fresh(name)
    assert name.decision == "unreviewed"
    assert [row.decision for row in name.reviews.order_by("expected_version")] == [
        "approved",
        "ignored",
        "unreviewed",
    ]


def test_staff_review_automatic_and_by_hand_changes(response_service):
    """Staff review every census change, By hand ones too (the review
    pipeline's decisions 1 and 2, #1683): an accepted By hand change waits
    to be entered by hand, an accepted Automatic one for publication."""
    harness, _, staff, home, name = prepared(response_service)
    review(harness, staff, home, "approved", note="Checked")
    review(harness, staff, name, "approved")
    for row in (fresh(home), fresh(name)):
        assert (row.decision, row.execution) == ("approved", "pending")
    assert set(ProposalReview.objects.values_list("actor_id", flat=True)) == {staff}


def test_service_refuses_leaders_stale_and_unchanged_reviews(response_service):
    """A Ministry leader never reviews census changes; a stale version, an
    unchanged decision and an over-long note are refused."""
    harness, admin, staff, home, name = prepared(response_service)
    with pytest.raises(PermissionError):
        review(harness, leader(harness, admin), name, "approved")
    with pytest.raises(StaleRecordError):
        review(harness, admin, name, "approved", version=name.version + 1)
    with pytest.raises(ValueError):
        review(harness, admin, name, "unreviewed")
    with pytest.raises(ValueError):
        review(harness, admin, name, "approved", note="x" * 2001)
    assert ProposalReview.objects.count() == 0


@pytest.mark.parametrize(
    ("execution", "decision", "allowed"),
    [
        ("failed", "approved", True),
        ("conflict", "ignored", True),
        ("conflict", "approved", False),
        ("queued", "ignored", False),
        ("published", "ignored", False),
        ("resolved_upstream", "approved", False),
    ],
)
def test_only_an_open_change_is_reviewed(
    response_service, execution, decision, allowed
):
    """Pending, failed or in conflict only, never queued for publication or
    final; a conflict is not approved until it is resolved."""
    harness, admin, _, _, name = prepared(response_service)
    owner(
        "UPDATE stewardship_proposed_change SET execution=%s WHERE id=%s",
        [execution, name.pk],
    )
    name = fresh(name)
    if allowed:
        review(harness, admin, name, decision)
        assert fresh(name).decision == decision
        return
    with pytest.raises(ValueError):
        review(harness, admin, name, decision)
    with pytest.raises((IntegrityError, DatabaseError), match="current authority"):
        forge(name, admin, decision, update=False)
    assert ProposalReview.objects.count() == 0


@pytest.mark.parametrize("decision", ["ignored", "unreviewed"])
def test_a_review_after_confirmation_leaves_the_plan_stale(confirmed, decision):  # noqa: F811
    """A review advances the version, so the confirmed plan that included the
    approved change no longer matches: the worker writes nothing, the change
    is never queued and keeps the decision the review gave it."""
    state = confirmed
    review(state.harness, state.admin, state.first, decision)
    assert run(state).invalidated == 1
    assert state.gateway.writes == []
    assert results(state.entity) == [("invalidated", "")]
    first = fresh(state.first)
    assert (first.execution, first.decision) == ("pending", decision)


def forge(proposal, actor, decision, *, update=True, assignments=None, version=None):
    """Insert a review row and, unless ``update`` is false, apply ``assignments``
    (the review's own decision by default) directly, as the web login,
    bypassing the service and the audit, inside one transaction."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_proposal_review (id,correlation_id,actor_id,"
            "proposal_id,expected_version,request_key,decision,admin_value_set,"
            "admin_value,note) VALUES (gen_random_uuid(),gen_random_uuid(),%s,%s,"
            "%s,gen_random_uuid(),%s,false,NULL,'')",
            [
                actor,
                proposal.pk,
                proposal.version if version is None else version,
                decision,
            ],
        )
        if update:
            cursor.execute(
                "UPDATE stewardship_proposed_change SET "
                f"{assignments or 'decision=%s'}, version=version+1 WHERE id=%s",
                [proposal.pk] if assignments else [decision, proposal.pk],
            )


@pytest.mark.parametrize(
    "case", ["leader", "reopen_without_note", "stale", "unchanged", "unknown"]
)
def test_sql_refuses_a_forged_review(response_service, case):
    """The review guard holds even when the service is bypassed: a Ministry
    leader, an ignored change sent back without a note (#1683), a stale
    version, an unchanged decision and an unknown actor are refused."""
    harness, admin, staff, home, name = prepared(response_service)
    if case == "reopen_without_note":
        review(harness, staff, home, "ignored")
        home = fresh(home)
    target, actor, decision, version = {
        "leader": (name, leader(harness, admin) if case == "leader" else None),
        "reopen_without_note": (home, staff),
        "stale": (name, admin),
        "unchanged": (name, admin),
        "unknown": (name, uuid4()),
    }[case] + {
        "reopen_without_note": ("unreviewed", None),
        "stale": ("approved", name.version + 1),
        "unchanged": ("unreviewed", None),
    }.get(case, ("approved", None))
    before = target.decision
    with pytest.raises((IntegrityError, DatabaseError), match="current authority"):
        forge(target, actor, decision, version=version)
    assert fresh(target).decision == before


def test_sql_refuses_a_review_without_its_audit(response_service):
    """A review row and its decision, with no audit event, cannot commit; nor
    can a review row alone."""
    harness, admin, _, _, name = prepared(response_service)
    for update in (True, False):
        with pytest.raises((IntegrityError, DatabaseError), match="result and audit"):
            forge(name, admin, "approved", update=update)
    assert ProposalReview.objects.count() == 0
    assert fresh(name).decision == "unreviewed"


@pytest.mark.parametrize(
    "assignments",
    [
        "decision='ignored'",
        "decision='approved', execution='queued'",
        "decision='approved', superseded_by_id=id",
        "decision='approved', admin_value_set=true, admin_value='\"x\"'",
        "decision='approved', current_available=NOT current_available",
    ],
)
def test_sql_refuses_an_update_that_differs_from_its_review(
    response_service, assignments
):
    """A valid review row admits exactly its own result, nothing more."""
    harness, admin, _, _, name = prepared(response_service)
    # Refused by the guard, or, for a column the web login may not update at
    # all, by its grants.
    with pytest.raises(DatabaseError):
        forge(name, admin, "approved", assignments=assignments)
    name = fresh(name)
    assert (name.execution, name.decision) == ("pending", "unreviewed")


def test_review_history_is_immutable(response_service):
    """A recorded review can never be changed or removed."""
    harness, admin, _, _, name = prepared(response_service)
    recorded = review(harness, admin, name, "approved", note="Checked")
    for statement in (
        "UPDATE stewardship_proposal_review SET note='rewritten' WHERE id=%s",
        "DELETE FROM stewardship_proposal_review WHERE id=%s",
    ):
        with (
            pytest.raises((IntegrityError, DatabaseError)),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, [recorded.pk])


def test_do_block_refuses_the_earlier_guard_and_installs_on_a_bare_database():
    """The file's closing check passes as installed and raises once the
    derived guard is put back to 0090's body; the whole file, run on a
    database with that body and no review table, installs everything."""
    from .test_publication_worker_postgresql import _replaced

    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    earlier = _replaced(
        (MIGRATIONS / "migrations/0090_publication_worker.sql").read_text(
            encoding="utf-8"
        ),
        "stewardship_response_derived_guard_v1",
    )
    own = _replaced(text, "stewardship_proposal_review_guard_v1")

    class Rollback(Exception):
        """Ends a check that must leave the database as it found it."""

    # 0128 (#1683) replaced the review guard since: each check runs on this
    # file's own body for it, rolled back, so it tests this file alone.
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(own)
        cursor.execute(check)
        raise Rollback
    with (
        pytest.raises(DatabaseError, match="lacks the review branch"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(own)
        cursor.execute(earlier)
        cursor.execute(check)
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(earlier)
        cursor.execute("DROP TABLE stewardship_proposal_review")
        cursor.execute(
            "DROP FUNCTION stewardship_proposal_review_guard_v1(),"
            " stewardship_proposal_review_effect_v1()"
        )
        cursor.execute(text)
        cursor.execute(
            "SELECT prosrc LIKE '%%stewardship_proposal_review v%%'"
            " FROM pg_proc WHERE proname='stewardship_response_derived_guard_v1'"
        )
        assert cursor.fetchone() == (True,)
        raise Rollback
