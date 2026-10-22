"""A Family's later census answers and the review pipeline (#1683).

The pipeline's resubmission rule while Families see their own answers
(docs/specs/stewardship/review-pipeline/spec.md, "Resubmission while the
setting is off") compares each census item with the Family's previous
answer. These tests pin the census cases the rule names.
"""

from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Decision
from parishkit.stewardship.reports.census_changes import REOPENED, census_rows
from parishkit.stewardship.reports.census_worklist import worklist_rows
from parishkit.stewardship.responses import effective
from parishkit.stewardship.responses.census_review import resolve_census_conflict
from parishkit.stewardship.responses.models import ProposedChange

from .test_background_grants_postgresql import task_login
from .test_census_conflict_postgresql import conflicted
from .test_census_pipeline_review_postgresql import decide, prepared
from .test_census_review_postgresql import fresh
from .test_ministry_responses_postgresql import respond, revisit
from .test_publication_plans_postgresql import owner
from .test_response_http_postgresql import answers_for
from .test_runtime_auth_grants_postgresql import web_login

# These test the rule before the reviewed Family form (#1705): forms drawn
# before its release, as one open across the deploy is.
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("earlier_rule"),
]


def again(harness, **members):
    """The Family submits again: its own last answers, with Member 3's
    ``members`` fields changed."""
    with web_login():
        form = revisit(harness)
        answers = answers_for(form)
        answers["members"]["3"] |= members
        return respond(harness, form, answers)


def rows_for(field):
    """Every census row of ``field``, oldest first."""
    return list(
        ProposedChange.objects.filter(field=field).order_by(
            "submission__family_version"
        )
    )


@pytest.mark.parametrize("field", ["prefix", "first_name"])
def test_an_unchanged_answer_after_entry_keeps_the_entered_change(
    response_service, field
):
    """An unchanged answer for a change already Entered by hand (or written
    to ParishSoft), not yet in ParishSoft's download, keeps that change and
    creates no row, however many times the Family answers again; a
    different answer later is a new change To review, against ParishSoft's
    value (#1683). Before #1683 each unchanged answer made a duplicate
    unreviewed row."""
    harness, admin, staff, rows = prepared(response_service)
    if field == "prefix":
        decide(harness, staff, [rows["prefix"]], "entered_by_hand")
        final = "resolved_external"
    else:
        # As the publication worker leaves a written change.
        owner(
            "UPDATE stewardship_proposed_change SET decision='approved',"
            " execution='published' WHERE id=%s",
            [rows["first_name"].pk],
        )
        final = "published"
    again(harness)
    again(harness)
    assert [row.execution for row in rows_for(field)] == [final]
    value = "Rev." if field == "prefix" else "Changed again"
    again(harness, **{field: value})
    found = rows_for(field)
    assert [(row.decision, row.execution) for row in found] == [
        (found[0].decision, final),
        ("unreviewed", found[1].execution),
    ]
    assert found[1].submitted_value == value
    assert found[1].execution in ("pending", "conflict")


def test_an_answer_set_back_is_not_kept_as_the_entered_change(response_service):
    """A Family that sets the field back to ParishSoft's value after the
    entry, then answers the entered value again, asks for it anew: the
    entered change is not carried past the different answer."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    baseline = rows["prefix"].baseline_value or ""
    again(harness, prefix=baseline)
    assert len(rows_for("prefix")) == 1
    again(harness, prefix="Dr.")
    assert [row.execution for row in rows_for("prefix")] == [
        "resolved_external",
        "pending",
    ]


def test_the_database_refuses_an_answer_set_back_kept_as_entered(
    response_service, monkeypatch
):
    """The database's completeness check holds the same rule: the Family's
    answer again keeps an entered change only when its prior census
    response gave that answer too. Here an answer set back to ParishSoft's
    value came in between, so a derivation that treats the entered value
    again as kept (makes no row) is refused, not committed (#1716)."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    again(harness, prefix=rows["prefix"].baseline_value or "")
    # A derivation that keeps the entered change whatever the prior
    # census response answered: the newest earlier row alone decides.
    monkeypatch.setattr(effective, "same_answer", lambda *args: True)
    with pytest.raises(IntegrityError, match="atomic derived work"):
        again(harness, prefix="Dr.")
    assert [row.execution for row in rows_for("prefix")] == ["resolved_external"]


def test_an_answer_over_an_accepted_base_comes_back_as_a_conflict(response_service):
    """An approval given over ParishSoft's changed value (an accepted base)
    is not carried to the Family's same answer: the new row is back to
    review against the original baseline, and the worklist says so
    rather than reopening it silently (#1683)."""
    harness, admin, _, rows = prepared(response_service)
    name = conflicted(rows["first_name"])
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        resolve_census_conflict(
            harness.service.store,
            admin,
            name.pk,
            expected_version=name.version,
            request_key=uuid4(),
            note="",
        )
    assert (fresh(name).decision, fresh(name).execution) == ("approved", "pending")
    again(harness)
    earlier, successor = rows_for("first_name")
    assert (earlier.execution, earlier.superseded_by_id) == ("superseded", successor.pk)
    # Back to review, against the original baseline: a Conflict while
    # ParishSoft still holds its changed value (here the test's snapshot
    # never changed, so it reads as pending).
    assert successor.decision == "unreviewed"
    assert (successor.baseline_available, successor.baseline_value) == (
        earlier.baseline_available,
        earlier.baseline_value,
    )
    # The worklist says why it is back, with the Family as actor.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        shown = {row["id"]: row for row in census_rows(harness.campaign.pk)}
    row = worklist_rows([shown[str(successor.pk)]])[0]
    assert row["reopened"] and row["decided_by"] == "Family"
    assert row["note"] == REOPENED


def test_a_real_parishsoft_change_brings_the_conflict_back_held(
    live_response_service,
):
    """With ParishSoft's value really changed (a promoted download), an
    approval over it is not carried to the Family's same answer: the new
    row is a Conflict that shows the earlier Accepted, not in force, and why
    it is back. A different answer is a new request, with no such line."""
    from .response_builders import response_source
    from .test_census_conflict_postgresql import accept
    from .test_response_revisit_postgresql import revisit as revisit_form
    from .test_response_submission_postgresql import form_and_answers, submit
    from .test_source_families_postgresql import prepare, promote

    harness = live_response_service
    earlier, claim = prepare(response_source())
    promote(earlier, claim, harness.campaign, harness.rings)
    form, answers = form_and_answers(harness)
    answers["members"]["3"]["first_name"] = "Requested name"
    submit(harness, form, answers)
    data = response_source()
    data.members[3]["firstName"] = "Updated source"
    current, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(current, claim, harness.campaign, harness.rings)
    accept(ProposedChange.objects.get(field="first_name"))
    harness, form, answers, _ = revisit_form(harness)
    with web_login():
        submit(harness, form, answers)
    successor = ProposedChange.objects.get(field="first_name", superseded_by=None)
    assert (successor.decision, successor.execution) == ("unreviewed", "conflict")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        shown = {row["id"]: row for row in census_rows(harness.campaign.pk)}
    row = worklist_rows([shown[str(successor.pk)]])[0]
    assert row["placement"].held_decision is Decision.ACCEPTED
    assert (row["decided_by"], row["note"]) == ("Family", REOPENED)
    harness, form, answers, _ = revisit_form(harness)
    answers["members"]["3"]["first_name"] = "Another name"
    with web_login():
        submit(harness, form, answers)
    latest = ProposedChange.objects.get(field="first_name", superseded_by=None)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        shown = {row["id"]: row for row in census_rows(harness.campaign.pk)}
    assert not shown[str(latest.pk)]["reopened"]
