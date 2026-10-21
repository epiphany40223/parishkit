"""The refresh re-evaluation of hand-entered pledges (#1704).

Frozen migration 0134 (provisional number,
``schema/migrations/0134_pledge_refresh_checks.sql``) adds the pledge refresh
checks, the upcoming-year pledge selection and the mark's amount. These tests
promote real refreshes of synthetic giving (the campaign's upcoming period is
2055 on fund 9) and read the real page rows: an accepted pledge is Confirmed
when ParishSoft holds the amount carried forward, an entered one Entered
incorrectly at a third amount and Not seen in ParishSoft after two full
refreshes at its mark; an Ignored one is only noted; a changed pledge leaves
its checks behind; and the guard refuses every check the snapshot does not
bear out (review pipeline spec, "Financial stream", Pledge confirmation).
"""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Decision, Stage
from parishkit.stewardship.reports.pledge_models import (
    PledgeRefreshCheck,
    PledgeReview,
)
from parishkit.stewardship.reports.pledge_review_views import pledge_rows
from parishkit.stewardship.responses.financial_inputs import financial_definition
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.cursors import refresh_cursor
from parishkit.stewardship.source.leases import acquire_source
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    finish_snapshot,
    stage_entities,
)
from parishkit.stewardship.web.review_worklist import NOT_SEEN

from ..financial_factory import record
from ..test_source_corpus import TODAY
from .response_builders import response_source
from .source_builders import running_source_task
from .test_background_grants_postgresql import task_login
from .test_pledge_review_postgresql import admin, again, decide, pledged
from .test_refresh_checks_postgresql import started  # noqa: F401
from .test_source_families_postgresql import promote
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)

# The Family's answer in ``pledged`` (test_financial_report_postgresql.pledge).
ANSWER = "1234.50"


def giving(harness, *amounts, covered=True, family="1"):
    """Promote a full refresh whose upcoming year holds these pledges.

    Each amount is one ParishSoft pledge of the harness Family on fund 9
    dated in 2055, the campaign's upcoming period; the comparison period's
    pledge (2026) is always there and never counts. ``covered`` False
    stages no giving proof, as a refresh whose giving read was incomplete.
    """
    values = harness.campaign.active_configuration.values
    definition = financial_definition(values, campaign_id=harness.campaign.pk)
    # The financial fixture's tenant and Family groups
    # (test_financial_source_postgresql.financial_source).
    data = replace(response_source(), organization_id=12345)
    for found in data.families.values():
        if found.get("registeredOrganizationID") == 5:
            found["registeredOrganizationID"] = 12345
    corpus = normalize_core(data, as_of=TODAY)
    corpus["pledge"] = {
        "201": record("1200.00", effective_date="2026-01-01"),
        **{
            str(500 + index): record(
                amount, effective_date="2055-03-01", family_key=family
            )
            for index, amount in enumerate(amounts)
        },
    }
    claim = acquire_source(**running_source_task(), phase="full")
    snapshot = begin_snapshot(claim, organization_id=12345, admit=permit)
    for kind, entities in corpus.items():
        stage_entities(snapshot.pk, claim, kind=kind, entities=entities, admit=permit)
    as_of = snapshot.started_at.date().isoformat()
    cursor = refresh_cursor(
        snapshot_id=snapshot.pk,
        kind="full",
        started_at=snapshot.started_at,
        window_digest=definition.window.digest,
        evidence={
            "schema": "source-load-v1",
            "window_digest": definition.window.digest,
            "as_of_date": as_of,
            "giving_as_of_date": as_of,
        },
    )
    finish_snapshot(
        snapshot.pk,
        claim,
        expected_counts={kind: len(rows) for kind, rows in corpus.items()},
        cursor=cursor if covered else {},
        admit=permit,
    )
    return snapshot, claim


def refresh(harness, *amounts, **options):
    """Stage and promote one full refresh (``giving``) as the database owner."""
    snapshot, claim = giving(harness, *amounts, **options)
    return promote(snapshot, claim, harness.campaign, harness.rings)


def checks():
    """Every pledge refresh check's (outcome, amount), oldest first."""
    return list(
        PledgeRefreshCheck.objects.order_by("created_at", "id").values_list(
            "outcome", "observed"
        )
    )


def row(harness, submission):
    """The Pledges to enter row of ``submission``, read as web."""
    from parishkit.stewardship.accounts.policy import current_principal

    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        principal = current_principal(harness.service.store, admin())
        rows, _ = pledge_rows(harness.campaign.pk, principal)
    return next(found for found in rows if found["item"].submission_id == submission.pk)


def accepted(response_service, *, marked=True, **answer):
    """A live pledge Accepted (and marked Entered by hand) by the Administrator."""
    harness, submission = pledged(response_service, **answer)
    decide(harness, admin(), "accept", [submission])
    if marked:
        decide(harness, admin(), "entered_by_hand", [submission])
    return harness, submission


def test_an_entered_pledge_is_confirmed_and_never_checked_again(response_service):
    """The mark records ParishSoft's upcoming amount ($0.00: none yet); the
    refresh finding the Family's amount Confirms the pledge, keeping its
    Accepted and its mark, and it is never checked again."""
    harness, submission = accepted(response_service)
    mark = PledgeReview.objects.order_by("-sequence").first()
    assert (mark.marked_pledge_known, mark.marked_pledge) == (True, Decimal("0.00"))
    refresh(harness, "1000.00", "234.50")
    assert checks() == [("confirmed", Decimal(ANSWER))]
    check = PledgeRefreshCheck.objects.get()
    assert check.full_refresh and check.item_version == mark.sequence
    assert check.review_id == mark.pk
    found = row(harness, submission)
    assert found["placement"].stage is Stage.CONFIRMED
    assert found["placement"].decision is Decision.ACCEPTED
    refresh(harness, "1.00")
    assert checks() == [("confirmed", Decimal(ANSWER))]


def test_not_seen_after_two_full_refreshes_at_the_mark(response_service, started):  # noqa: F811
    """A pledge ParishSoft still does not hold is flagged Not seen after two
    full refreshes, without changing its stage; the checks then stop."""
    harness, submission = accepted(response_service)
    refresh(harness)
    assert checks() == [("unchanged", Decimal("0.00"))]
    assert not row(harness, submission)["not_seen"]
    refresh(harness)
    found = row(harness, submission)
    assert found["not_seen"] and found["placement"].stage is Stage.ENTERED_BY_HAND
    refresh(harness)
    assert [outcome for outcome, _ in checks()] == ["unchanged", "unchanged"]


def test_a_third_amount_is_entered_incorrectly_until_corrected(
    response_service,
    started,  # noqa: F811
):
    """A typo at entry is Entered incorrectly, checked again only when the
    amount changes, and Confirmed once ParishSoft holds the Family's."""
    harness, submission = accepted(response_service)
    refresh(harness, "1243.50")
    assert checks() == [("entered_incorrectly", Decimal("1243.50"))]
    found = row(harness, submission)
    assert found["placement"].stage is Stage.ENTERED_INCORRECTLY
    refresh(harness, "1243.50")
    refresh(harness)  # back at the mark: no new evidence
    assert len(checks()) == 1
    refresh(harness, "1300.00")
    refresh(harness, ANSWER)
    assert [outcome for outcome, _ in checks()] == [
        "entered_incorrectly",
        "entered_incorrectly",
        "confirmed",
    ]
    assert row(harness, submission)["placement"].stage is Stage.CONFIRMED


def test_an_accepted_pledge_parishsoft_holds_is_confirmed_and_closed(
    response_service,
):
    """Rule 1 before the mark: To enter by hand is Confirmed, and nothing is
    offered on it then; a third amount (a Conflict) records nothing."""
    harness, submission = accepted(response_service, marked=False)
    refresh(harness, "99.00")
    assert checks() == []
    refresh(harness, ANSWER)
    assert row(harness, submission)["placement"].stage is Stage.CONFIRMED
    with pytest.raises(ValueError):
        decide(harness, admin(), "entered_by_hand", [submission])


def test_an_edited_pledge_is_judged_by_the_reviewers_amount(response_service):
    """C is the Edit's amount: the Family's in ParishSoft confirms nothing."""
    harness, submission = pledged(response_service)
    decide(
        harness,
        admin(),
        "edit",
        [submission],
        value_amount="1300",
        value_frequency="monthly",
    )
    decide(harness, admin(), "entered_by_hand", [submission])
    refresh(harness, ANSWER)
    assert checks() == [("entered_incorrectly", Decimal(ANSWER))]
    refresh(harness, "1300.00")
    found = row(harness, submission)
    assert found["placement"].stage is Stage.CONFIRMED
    assert found["placement"].decision is Decision.ACCEPTED_EDITED


def test_an_ignored_pledge_is_only_noted(response_service):
    """ParishSoft holding an Ignored pledge's answer is noted; it stays Ignored."""
    harness, submission = pledged(response_service)
    decide(harness, admin(), "ignore", [submission])
    refresh(harness, ANSWER)
    assert checks() == [("ignored_now_matches", Decimal(ANSWER))]
    found = row(harness, submission)
    assert found["placement"].stage is Stage.IGNORED
    refresh(harness)
    assert [outcome for outcome, _ in checks()] == ["ignored_now_matches", "unchanged"]


def test_a_zero_pledge_is_confirmed_by_no_pledge(response_service):
    """No pledge reads as $0.00, so a Family pledging nothing is Confirmed."""
    harness, submission = accepted(response_service, annual_pledge="0")
    refresh(harness)
    assert checks() == [("confirmed", Decimal("0.00"))]


def test_a_changed_pledge_leaves_its_checks_behind(response_service):
    """The Family answering again with another pledge reopens it: the checks
    under the earlier decision no longer count, and To review gets none."""
    harness, submission = accepted(response_service, marked=False)
    refresh(harness, ANSWER)
    changed = again(harness, annual_pledge="99")
    found = row(harness, changed)
    assert found["placement"].stage is Stage.TO_REVIEW and found["check"] is None
    refresh(harness, "99.00")
    assert len(checks()) == 1


def test_no_giving_proof_and_no_family_check_nothing(response_service):
    """Without a proven giving read, or with the Family gone, nothing is
    checked; a mark then records no amount."""
    harness, submission = accepted(response_service, marked=False)
    refresh(harness, ANSWER, covered=False)
    assert checks() == []
    decide(harness, admin(), "entered_by_hand", [submission])
    mark = PledgeReview.objects.order_by("-sequence").first()
    assert not mark.marked_pledge_known and mark.marked_pledge is None
    assert row(harness, submission)["placement"].stage is Stage.ENTERED_BY_HAND


def test_the_worker_login_confirms_a_pledge(response_service):
    """Promotion on the source worker's own login reads what the checks need
    and inserts them under its grants (the other tests promote as the
    database owner)."""
    harness, submission = accepted(response_service)
    snapshot, claim = giving(harness, ANSWER)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    assert checks() == [("confirmed", Decimal(ANSWER))]


def test_the_stage_filter_lists_the_flag_and_the_new_stages(response_service):
    """The page counts Confirmed and offers Not seen only while flagged."""
    from parishkit.stewardship.reports.pledge_review_views import stage_table

    harness, submission = accepted(response_service)
    refresh(harness, ANSWER)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        from parishkit.stewardship.accounts.policy import current_principal

        principal = current_principal(harness.service.store, admin())
        rows, _ = pledge_rows(harness.campaign.pk, principal)
    _table, counts = stage_table(
        rows, Stage.CONFIRMED.value, {"paging": {}}, action="/x/"
    )
    assert counts[Stage.CONFIRMED.value] == 1 and NOT_SEEN not in counts


def test_the_guard_admits_only_the_promotion_and_keeps_checks(response_service):
    """Outside a promotion no check can be written, even by the owner; a
    check is never updated or deleted."""
    harness, submission = accepted(response_service)
    mark = PledgeReview.objects.order_by("-sequence").first()
    with pytest.raises(DatabaseError), transaction.atomic():
        PledgeRefreshCheck.objects.create(
            snapshot_id=uuid4(),
            full_refresh=True,
            review=mark,
            item_version=mark.sequence,
            outcome="confirmed",
            observed=Decimal(ANSWER),
        )
    refresh(harness, ANSWER)
    check = PledgeRefreshCheck.objects.get()
    with connection.cursor() as cursor:
        for statement in (
            "UPDATE stewardship_pledge_refresh_check SET outcome='unchanged' "
            "WHERE id=%s",
            "DELETE FROM stewardship_pledge_refresh_check WHERE id=%s",
        ):
            with pytest.raises(DatabaseError), transaction.atomic():
                cursor.execute(statement, [check.pk])


# Each forged check, as a promotion of ParishSoft holding ``amounts`` would
# write it for the marked (or Ignored) pledge: its fields, and the guard's
# words.
FORGERIES = {
    "amount not the snapshot's": (
        {"outcome": "confirmed", "observed": Decimal(ANSWER)},
        (),
        "snapshot's pledge",
    ),
    "confirmed at another amount": (
        {"outcome": "confirmed", "observed": Decimal("5.00")},
        ("5.00",),
        "differs from its",
    ),
    "unchanged at the carried amount": (
        {"outcome": "unchanged", "observed": Decimal(ANSWER)},
        (ANSWER,),
        "differs from its",
    ),
    "entered incorrectly at the mark": (
        {"outcome": "entered_incorrectly", "observed": Decimal("0.00")},
        (),
        "differs from its",
    ),
    "ignored outcome on an accepted pledge": (
        {"outcome": "ignored_now_matches", "observed": Decimal(ANSWER)},
        (ANSWER,),
        "differs from its",
    ),
    "an earlier sequence": (
        {"outcome": "unchanged", "observed": Decimal("0.00"), "item_version": 1},
        (),
        "decided pledge in force",
    ),
    "a quick refresh called full": (
        {"outcome": "unchanged", "observed": Decimal("0.00"), "full_refresh": False},
        (),
        "promoting source worker",
    ),
}


@pytest.mark.parametrize("case", sorted(FORGERIES))
def test_the_guard_refuses_each_forgery_inside_a_promotion(
    response_service, monkeypatch, case
):
    """Each of the guard's refusals, met inside a real promotion: the refresh
    is rolled back and no forged check is written."""
    from parishkit.stewardship.responses import refresh_checks

    fields, amounts, words = FORGERIES[case]
    harness, submission = accepted(response_service)
    mark = PledgeReview.objects.order_by("-sequence").first()

    def forged(snapshot, corpus, campaign_id, full):
        """One check that the snapshot does not bear out."""
        yield PledgeRefreshCheck(
            **(
                {
                    "snapshot_id": snapshot.pk,
                    "full_refresh": full,
                    "review_id": mark.pk,
                    "item_version": mark.sequence,
                }
                | fields
            )
        )

    monkeypatch.setattr(refresh_checks, "_checks", forged)
    with pytest.raises(DatabaseError, match=words):
        refresh(harness, *amounts)
    assert not PledgeRefreshCheck.objects.exists()


def test_the_mark_guard_refuses_an_amount_not_the_snapshots(
    response_service, monkeypatch
):
    """The pledge review guard admits a mark's amount only when it is the
    current snapshot's own."""
    from parishkit.stewardship.reports import pledge_review

    harness, submission = accepted(response_service, marked=False)
    monkeypatch.setattr(pledge_review, "marked_pledge", lambda *_: Decimal("7.00"))
    with pytest.raises(DatabaseError, match="authority, version or state"):
        decide(harness, admin(), "entered_by_hand", [submission])
    assert not PledgeReview.objects.filter(entered=True).exists()


def test_the_mark_guard_refuses_a_known_mark_without_an_amount(
    response_service, monkeypatch
):
    """A mark that says it knows ParishSoft's amount must carry one, even
    when the current snapshot has none to compare with (no giving read):
    such a row would leave the refresh nothing to compare."""
    from parishkit.stewardship.reports import pledge_models, pledge_review

    harness, submission = accepted(response_service, marked=False)
    refresh(harness, covered=False)
    original = pledge_models.PledgeReview.objects.create

    def known_none(**fields):
        """Record the mark as known with no amount."""
        if fields.get("entered"):
            fields["marked_pledge_known"] = True
        return original(**fields)

    monkeypatch.setattr(pledge_review.PledgeReview.objects, "create", known_none)
    with pytest.raises(DatabaseError, match="authority, version or state"):
        decide(harness, admin(), "entered_by_hand", [submission])
    assert not PledgeReview.objects.filter(entered=True).exists()


def test_the_migration_check_refuses_the_old_pledge_review_guard(response_service):
    """0134's DO block refuses to commit while 0127's guard, which admits no
    mark amount, is installed."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
    old = (root / "migrations/0127_pledge_review.sql").read_text(encoding="utf-8")
    new = (root / "migrations/0134_pledge_refresh_checks.sql").read_text(
        encoding="utf-8"
    )
    start = old.index("CREATE FUNCTION public.stewardship_pledge_review_guard_v1()")
    body = old[start : old.index("END $$;", start) + len("END $$;")]
    block = new[new.index("DO $check$") :]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(body.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))
        with pytest.raises(DatabaseError, match="mark's pledge"), transaction.atomic():
            cursor.execute(block)
        transaction.set_rollback(True)
    with connection.cursor() as cursor:
        cursor.execute(block)


def test_the_quick_update_check_agrees_with_what_promotion_writes(response_service):
    """``refresh_checks_current`` answers False exactly while a promotion of
    the same snapshot would write a pledge check, and True once it has."""
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.responses.refresh_checks import (
        refresh_checks_current,
    )
    from parishkit.stewardship.source.families import PreparedPromotion
    from parishkit.stewardship.source.leases import release_source

    def promoted():
        """Promote a refresh holding the Family's amount; its snapshot."""
        snapshot, claim = giving(harness, ANSWER)
        promote(snapshot, claim, harness.campaign, harness.rings)
        snapshot.refresh_from_db()
        return snapshot

    harness, submission = pledged(response_service)
    held = promoted()
    decide(harness, admin(), "accept", [submission])

    def current():
        """The check as a quick update makes it: a staging snapshot just
        begun (no rows, no cursor yet), with the current snapshot's corpus."""
        claim = acquire_source(**running_source_task(), phase="delta")
        staging = begin_snapshot(claim, organization_id=12345, admit=permit)
        corpus = PreparedPromotion.of(held).corpus
        try:
            with work_transaction():
                return refresh_checks_current(
                    staging, corpus, campaign_id=harness.campaign.pk
                )
        finally:
            release_source(claim)

    assert not current()
    held = promoted()
    assert checks() == [("confirmed", Decimal(ANSWER))]
    assert current()
