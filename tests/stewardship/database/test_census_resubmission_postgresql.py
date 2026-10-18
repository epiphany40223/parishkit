"""A Family's later census answers and the review pipeline (#1683).

The pipeline's resubmission rule while Families see their own answers
(docs/specs/stewardship/review-pipeline/spec.md, "Resubmission while the
setting is off") compares each census item with the Family's previous
answer. These tests pin the census cases the rule names.
"""

from uuid import uuid4

import pytest
from django.db import transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.census_changes import REOPENED, census_rows
from parishkit.stewardship.reports.census_worklist import worklist_rows
from parishkit.stewardship.responses.census_review import resolve_census_conflict
from parishkit.stewardship.responses.models import ProposedChange

from .test_background_grants_postgresql import task_login
from .test_census_conflict_postgresql import conflicted
from .test_census_pipeline_review_postgresql import decide, prepared
from .test_census_review_postgresql import fresh
from .test_ministry_responses_postgresql import respond, revisit
from .test_response_http_postgresql import answers_for
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


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


def test_an_unchanged_answer_after_entry_creates_a_new_row_today(response_service):
    """Today's behaviour, pinned before #1683 changes it: an unchanged
    answer for a change already Entered by hand (not yet in ParishSoft's
    download) is not "same intent", so it creates a fresh unreviewed row."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["prefix"]], "entered_by_hand")
    again(harness)
    found = rows_for("prefix")
    assert [(row.decision, row.execution) for row in found] == [
        ("unreviewed", "resolved_external"),
        ("unreviewed", "pending"),
    ]


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
