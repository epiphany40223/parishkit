"""A pledge Edit carries the reviewer's share methods (#2177, frozen file 0181).

The pledge editor's Edit records the reviewer's share methods on the same
``stewardship_pledge_review`` row as the amount and frequency, beside the
Family's answer the row records (review-pipeline spec, "Financial stream").
These tests run the decision service, the Pledges rows and the reviewed
Family form on the restricted web login, the guard's refusals of rows the
service never writes, and the frozen file's own closing check.
"""

import re
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Action
from parishkit.stewardship.reports.pledge_models import PledgeReview
from parishkit.stewardship.reports.pledge_review import (
    DETAILS_HINT,
    SHARES_HINT,
    EditRefusal,
    PledgeDecisions,
    read_items,
    version_of,
)
from parishkit.stewardship.reports.pledge_review_views import (
    HEADINGS,
    decorate,
    export_rows,
    pledge_rows,
)
from parishkit.stewardship.web.review_worklist import StaleDecision, WorklistDecision

from ..sql_operators import bare_operators
from ..test_financial_answers import CHECK, OTHER
from .test_background_grants_postgresql import task_login
from .test_pledge_review_postgresql import Actor, admin, decide, pledged, state

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0181_pledge_share_methods.sql"
# Each replaced function and the frozen file holding the body 0181 copied.
PREVIOUS = {
    "stewardship_pledge_review_guard_v1": "0158_ignored_note_optional.sql",
    "stewardship_pledge_review_states_v1": "0155_reviewed_pledge_resubmission.sql",
}
SENT = {"value_shares": "1"}
DROP_COLUMN = "ALTER TABLE stewardship_pledge_review DROP COLUMN reviewed_shares"


def edit(harness, actor, submission, amount="1234.50", frequency="monthly", **shares):
    """An Edit through the service, with the editor's share-method fields."""
    return decide(
        harness,
        actor,
        "edit",
        [submission],
        value_amount=amount,
        value_frequency=frequency,
        **SENT,
        **shares,
    )


def rows(harness, actor):
    """Pledges' rows, read and decorated as the page draws them."""
    from parishkit.stewardship.accounts.policy import current_principal

    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        principal = current_principal(harness.service.store, actor)
        found, _metadata = pledge_rows(harness.campaign.pk, principal)
        return found, decorate(found)


def test_an_edit_records_the_reviewers_share_methods_beside_the_familys(
    response_service,
):
    """The Edit stores the reviewer's share methods; the row's answer keeps the
    Family's; Pledges shows and downloads both, and the editor starts from
    the reviewer's."""
    harness, submission = pledged(response_service)
    actor = admin()
    notice = edit(
        harness,
        actor,
        submission,
        **{f"value_share_{OTHER}": "on", f"value_share_text_{OTHER}": " Stock "},
    )
    assert notice == "The pledge accepted (edited)."
    row = PledgeReview.objects.get()
    assert (row.decision, row.reviewed_shares) == ("accepted_edited", {OTHER: "Stock"})
    assert row.answer["shares"] == {CHECK: ""}
    assert state(harness, submission).stage == "to_enter_by_hand"
    found, (drawn,) = rows(harness, actor)
    assert "Bank check" in str(drawn["family_answer"])
    assert "Other: Stock" in str(drawn["reviewer_value"])
    assert [share["label"] for share in drawn["reviewer_shares"]] == ["Other"]
    assert [(c["id"], c["checked"], c["text"]) for c in drawn["edit_shares"]] == [
        (CHECK, False, ""),
        (OTHER, True, "Stock"),
    ]
    cells = dict(zip(HEADINGS, export_rows(found, ZoneInfo("UTC"))[0], strict=True))
    assert cells["Share methods"] == "Bank check"
    assert cells["Reviewer's share methods"] == "Other: Stock"
    assert cells["Share methods to enter"] == "Other: Stock"
    # The mark carries them, and so does the undo of the mark.
    decide(harness, actor, "entered_by_hand", [submission])
    decide(harness, actor, "back_to_review", [submission], note="Typed wrong")
    assert [r.reviewed_shares for r in PledgeReview.objects.order_by("sequence")] == [
        {OTHER: "Stock"}
    ] * 3
    assert state(harness, submission).stage == "to_enter_by_hand"


def test_the_familys_own_share_methods_are_recorded_as_none(response_service):
    """Share methods equal to the Family's are no reviewer's value: with a new
    amount they are stored as none; with the Family's amount and frequency
    too the Edit is the Accept it is. A zero pledge has none."""
    harness, submission = pledged(response_service)
    actor = admin()
    edit(harness, actor, submission, amount="1300", **{f"value_share_{CHECK}": "on"})
    assert PledgeReview.objects.get().reviewed_shares is None
    _found, (drawn,) = rows(harness, actor)
    assert drawn["reviewer_value"] == "$1,300.00, Monthly"
    decide(harness, actor, "back_to_review", [submission])
    notice = edit(harness, actor, submission, **{f"value_share_{CHECK}": "on"})
    assert notice.endswith("accepted: the value matched the Family's answer.")
    decide(harness, actor, "back_to_review", [submission])
    edit(harness, actor, submission, amount="0", frequency="")
    latest = PledgeReview.objects.order_by("-sequence").first()
    assert (latest.action, latest.reviewed_shares) == ("edit", None)
    # An Edit without the share-method fields (pk-admin, a page drawn
    # before #2177) leaves them as they are: the Family's own here.
    decide(harness, actor, "back_to_review", [submission])
    decide(
        harness,
        actor,
        "edit",
        [submission],
        value_amount="1300",
        value_frequency="weekly",
    )
    assert PledgeReview.objects.order_by("-sequence").first().reviewed_shares is None


def test_an_edit_without_share_fields_keeps_an_edits_in_force(response_service):
    """A Conflict's Edit in force keeps its share methods through a pk-admin
    Edit of the amount alone (#2177), so they never quietly revert."""
    from parishkit.stewardship.reports import pledge_review

    harness, submission = pledged(response_service)
    actor = admin()
    edit(
        harness,
        actor,
        submission,
        **{f"value_share_{OTHER}": "on", f"value_share_text_{OTHER}": "Stock"},
    )
    service = PledgeDecisions(harness.service.store, harness.campaign.pk)
    item = state(harness, submission)
    fields = {"value_amount": ["1400"], "value_frequency": ["weekly"]}
    decision = WorklistDecision(
        Action.EDIT, {str(submission.pk): 1}, "", uuid4(), fields=fields
    )
    kept = service._shares(decision, item, submission, (Decimal("1400.00"), "weekly"))
    assert kept == {OTHER: "Stock"}
    assert service._shares(decision, item, submission, (Decimal("0"), None)) is None
    assert pledge_review.SHARES_SENT not in fields


@pytest.mark.parametrize(
    ("fields", "field", "message"),
    [
        ({}, f"value_share_{CHECK}", SHARES_HINT),
        ({f"value_share_{OTHER}": "on"}, f"value_share_text_{OTHER}", DETAILS_HINT),
        (
            {f"value_share_{OTHER}": "on", f"value_share_text_{OTHER}": "x" * 2001},
            f"value_share_text_{OTHER}",
            DETAILS_HINT,
        ),
    ],
)
def test_share_methods_the_family_form_would_refuse_are_refused_at_their_field(
    response_service, fields, field, message
):
    """None ticked for a pledge above zero, or Other without (or with too
    long) details: refused at the field, nothing recorded."""
    harness, submission = pledged(response_service)
    with pytest.raises(EditRefusal) as refused:
        edit(harness, admin(), submission, **fields)
    assert (refused.value.fields, refused.value.message) == ((field,), message)
    assert not PledgeReview.objects.exists()


@pytest.mark.parametrize(
    "fields",
    [
        {"value_share_33333333-3333-4333-8333-333333333333": "on"},
        {f"value_share_{CHECK}": "yes"},
        {f"value_share_text_{CHECK}": "x", f"value_share_{CHECK}": "on"},
        {"value_shares": "2", f"value_share_{CHECK}": "on"},
    ],
)
def test_a_forged_share_method_field_is_a_malformed_edit(response_service, fields):
    """A method not offered, a checkbox value or details the form never
    sends, or another marker is a ValueError (a 400), never recorded."""
    harness, submission = pledged(response_service)
    values = SENT | fields
    with pytest.raises(ValueError):
        decide(
            harness,
            admin(),
            "edit",
            [submission],
            value_amount="1300",
            value_frequency="monthly",
            **values,
        )
    assert not PledgeReview.objects.exists()


def test_a_stale_share_method_edit_is_refused(response_service):
    """An Edit posted against a version someone else moved is stale: nothing
    is recorded, and the first decision stays."""
    harness, submission = pledged(response_service)
    actor = admin()
    items = read_items(harness.campaign.pk)
    seen = version_of(items[submission.pk])
    decide(harness, actor, "accept", [submission])
    decide(harness, actor, "back_to_review", [submission])
    with (
        pytest.raises(StaleDecision),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        PledgeDecisions(harness.service.store, harness.campaign.pk).decide(
            Actor(actor),
            WorklistDecision(
                Action.EDIT,
                {str(submission.pk): seen},
                "",
                uuid4(),
                fields={
                    "value_amount": ["1234.50"],
                    "value_frequency": ["monthly"],
                    "value_shares": ["1"],
                    f"value_share_{OTHER}": ["on"],
                    f"value_share_text_{OTHER}": ["Stock"],
                },
            ),
        )
    assert [r.action for r in PledgeReview.objects.order_by("sequence")] == [
        "accept",
        "back_to_review",
    ]


def forge(submission, actor, *, shares, **values):
    """Insert a review row directly as the web login, with ``shares``."""
    row = dict(sequence=1, action="edit", decision="accepted_edited", entered=False)
    row |= {"amount": "1300.00", "frequency": "monthly"} | values
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_pledge_review (id,correlation_id,actor_id,"
            "family_id,submission_id,sequence,request_key,action,decision,"
            "reviewed_annual_pledge,reviewed_frequency,reviewed_shares,entered,"
            "answer,marked_pledge_known,note) VALUES (gen_random_uuid(),"
            "gen_random_uuid(),%s,%s,%s,%s,gen_random_uuid(),%s,%s,%s,%s,"
            "%s::jsonb,%s,'{}',false,%s)",
            [
                actor,
                submission.family_id,
                submission.pk,
                row["sequence"],
                row["action"],
                row["decision"],
                row["amount"],
                row["frequency"],
                shares,
                row["entered"],
                row.get("note", ""),
            ],
        )


GUARD = "Pledge review lacks current authority, version or state"
FORGED = {
    "accept_with_shares": (
        {"action": "accept", "decision": "accepted", "amount": None, "frequency": None},
        '{"x": ""}',
        "pledge_review_shares_shape",
    ),
    "zero_with_shares": (
        {"amount": "0.00", "frequency": None},
        '{"x": ""}',
        "pledge_review_shares_shape",
    ),
    "not_an_object": ({}, '["x"]', GUARD),
    "not_text": ({}, '{"x": 1}', GUARD),
    "too_long": ({}, '{"x": "' + "y" * 2001 + '"}', GUARD),
    "the_familys_own": ({}, f'{{"{CHECK}": ""}}', GUARD),
    "mark_changing_them": (
        {
            "sequence": 2,
            "action": "entered_by_hand",
            "entered": True,
            "amount": "1234.50",
        },
        '{"y": ""}',
        GUARD,
    ),
    "undo_changing_them": (
        {
            "sequence": 3,
            "action": "back_to_review",
            "amount": "1234.50",
            "note": "Typed wrong",
        },
        '{"y": ""}',
        GUARD,
    ),
    # A row the guard admits fails only its audit at commit.
    "admitted": ({}, '{"x": "Stock"}', "Pledge review requires its audit"),
}


@pytest.mark.parametrize("case", sorted(FORGED))
def test_the_guard_refuses_share_methods_the_service_never_writes(
    response_service, case
):
    """Share methods on a row other than an Edit above zero, of another
    shape, beyond the text limit, equal to the Family's own, or changed by
    a mark: each refused for its own reason."""
    harness, submission = pledged(response_service)
    actor = admin()
    if case in ("mark_changing_them", "undo_changing_them"):
        edit(
            harness,
            actor,
            submission,
            **{f"value_share_{OTHER}": "on", f"value_share_text_{OTHER}": "Stock"},
        )
    if case == "undo_changing_them":
        decide(harness, actor, "entered_by_hand", [submission])
    values, shares, reason = FORGED[case]
    before = PledgeReview.objects.count()
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match=reason),
        work_transaction(),
    ):
        forge(submission, actor, shares=shares, **values)
    assert PledgeReview.objects.count() == before


def test_the_reviewed_form_draws_the_reviewers_share_methods_and_keeps_them(
    response_service,
):
    """The reviewed Family form shows the reviewer's share methods; sent back
    as shown, the decision stays in force (0181's selection); changed, the
    pledge is To review again."""
    from .test_reviewed_form_postgresql import again

    harness, submission = pledged(response_service)
    edit(
        harness,
        admin(),
        submission,
        **{f"value_share_{OTHER}": "on", f"value_share_text_{OTHER}": "Stock"},
    )
    form, response = again(harness)
    assert form["financial"]["answers"]["shares"] == {OTHER: "Stock"}
    assert response.answers["financial"]["shares"] == {OTHER: "Stock"}
    found = state(harness, response)
    assert (found.stage, found.in_force) == ("to_enter_by_hand", True)

    # (Sending the Family's own first answer instead is unchanged too: the
    # decision was made on it, rule 1.)
    def other(answers):
        answers["financial"]["shares"] = {CHECK: "", OTHER: "Stock"}

    _form, changed = again(harness, other)
    assert state(harness, changed).stage == "to_review"


def test_the_timeline_names_the_reviewers_share_methods(response_service):
    """The Edit's Timeline line adds its share methods, by their labels."""
    from parishkit.stewardship.reports.timeline_reviews import (
        SHARE_LABELS,
        item_words,
        share_labels_for,
    )

    harness, submission = pledged(response_service)
    edit(
        harness,
        admin(),
        submission,
        **{f"value_share_{OTHER}": "on", f"value_share_text_{OTHER}": "Stock"},
    )
    labels = share_labels_for(harness.campaign.pk)
    row = {
        "stream": "pledge",
        "a": "edit",
        "c": "1234.50",
        "d": "monthly",
        "subject": f'{{"{OTHER}": "Stock"}}',
    }
    assert item_words(row, {SHARE_LABELS: labels}) == (
        "Pledge; Reviewer's value: $1,234.50, Monthly; share methods: Other: Stock"
    )


# --- The frozen file -------------------------------------------------------


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


def statement(text, name):
    """The ``CREATE OR REPLACE FUNCTION`` statement for ``name`` in ``text``."""
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    return text[start : text.index("$$;", start) + len("$$;")]


def previous(name):
    """``name``'s body before 0181, from the file it was copied from."""
    return statement((MIGRATIONS / PREVIOUS[name]).read_text(encoding="utf-8"), name)


def check():
    """0181's closing check: the grants it records first, and its DO block."""
    text = FROZEN.read_text(encoding="utf-8")
    start = text.index("SELECT set_config(")
    recorded = text[start : text.index("\nALTER TABLE", start)]
    return recorded + text[text.index("DO $check$") :]


def test_the_check_passes_as_installed():
    """A fresh install has the column, the check and both bodies."""
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check())
        raise Rollback


@pytest.mark.parametrize("name", sorted(PREVIOUS))
def test_the_check_refuses_each_previous_body(name):
    """Either function put back to its body before 0181 fails the check."""
    with (
        pytest.raises(DatabaseError, match="Migration 0181 .* did not replace"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(previous(name))
        cursor.execute(check())


def test_the_check_refuses_changed_grants():
    """A grant changed between the recorded state and the check fails it."""
    text = check()
    recorded, block = text[: text.index("DO $check$")], text[text.index("DO $check$") :]
    with (
        pytest.raises(DatabaseError, match="changed the functions' grants"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(recorded)
        cursor.execute(
            "GRANT EXECUTE ON FUNCTION "
            "public.stewardship_pledge_review_states_v1(uuid,uuid) TO PUBLIC"
        )
        cursor.execute(block)


def test_the_check_refuses_a_missing_column():
    """Without the column the check fails first."""
    with (
        pytest.raises(DatabaseError, match="did not add the column"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(DROP_COLUMN)
        cursor.execute(check())


def normalized(body):
    """``body`` without its ``--`` comments, its white space collapsed, and
    each qualified operator (#2166) turned back to the bare one it replaced."""
    body = re.sub(r"OPERATOR\(pg_catalog\.([^)]+)\)", r"\1", body)
    return " ".join(re.sub(r"--[^\n]*", "", body).split())


def test_each_new_body_is_its_previous_one_plus_the_share_methods():
    """Only the share-method clauses (and comments) changed in each body, so a
    copy of a stale predecessor fails here. Their operators are all
    qualified (#2166)."""
    text = FROZEN.read_text(encoding="utf-8")
    for name in PREVIOUS:
        assert not bare_operators(statement(text, name)), name
    old = normalized(previous("stewardship_pledge_review_states_v1"))
    new = normalized(statement(text, "stewardship_pledge_review_states_v1"))
    assert old.replace(
        "THEN b.answer->'shares' ELSE",
        "THEN coalesce(v.reviewed_shares,b.answer->'shares') ELSE",
    ) == (new)
    old = normalized(previous("stewardship_pledge_review_guard_v1"))
    new = normalized(statement(text, "stewardship_pledge_review_guard_v1"))
    carried = (
        "ROW(NEW.decision,NEW.reviewed_annual_pledge, NEW.reviewed_frequency)",
        "ROW(prior.decision,prior.reviewed_annual_pledge, prior.reviewed_frequency)",
        "ROW(NEW.decision,NEW.reviewed_annual_pledge,NEW.reviewed_frequency)",
        "ROW(prior.decision,prior.reviewed_annual_pledge,prior.reviewed_frequency)",
    )
    for row in carried:
        assert row in old
    # The mark's and the undo's carried rows gain the share methods, the
    # check is computed and refused, and nothing else changes.
    widened = (
        old.replace(
            carried[0],
            "ROW(NEW.decision,NEW.reviewed_annual_pledge, NEW.reviewed_frequency,"
            "NEW.reviewed_shares)",
        )
        .replace(
            carried[1],
            "ROW(prior.decision,prior.reviewed_annual_pledge, prior.reviewed_frequency,"
            "prior.reviewed_shares)",
        )
        .replace(
            carried[2],
            "ROW(NEW.decision,NEW.reviewed_annual_pledge,NEW.reviewed_frequency, "
            "NEW.reviewed_shares)",
        )
        .replace(
            carried[3],
            "ROW(prior.decision,prior.reviewed_annual_pledge,prior.reviewed_frequency, "
            "prior.reviewed_shares)",
        )
        .replace("offered boolean;", "offered boolean; shares_ok boolean;")
        .replace(
            "OR NOT coalesce(offered,false)",
            "OR NOT coalesce(offered,false) OR NOT coalesce(shares_ok,false)",
        )
    )
    shares = new[new.index("shares_ok:=CASE") : new.index(" IF family.id IS NULL")]
    assert widened.replace("END; IF family.id", "END; " + shares + " IF family.id") == (
        new
    )


def test_the_file_installs_its_bodies_over_the_previous_ones():
    """On a database as it was before 0181, the file installs the column,
    the check and both bodies, and its check passes; rolled back."""
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(DROP_COLUMN)
        for name in PREVIOUS:
            cursor.execute(previous(name))
        cursor.execute(FROZEN.read_text(encoding="utf-8"))
        cursor.execute(
            "SELECT count(*) FROM pg_proc WHERE NOT prosecdef"
            " AND prosrc LIKE '%%reviewed_shares%%' AND proname = ANY(%s)",
            [list(PREVIOUS)],
        )
        assert cursor.fetchone() == (2,)
        raise Rollback
