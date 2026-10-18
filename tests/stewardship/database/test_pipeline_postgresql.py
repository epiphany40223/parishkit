"""The review pipeline's stage mapping agrees with rows the real services store.

The unit tables (``tests/stewardship/test_pipeline.py``) cover every
combination; these drive census changes and Ministry requests through the
services and SQL guards as installed, then map the stored rows, so a
stored shape the mapping does not expect fails here (#1691).
"""

import pytest

from parishkit.stewardship import pipeline
from parishkit.stewardship.pipeline import Decision, Placement, Stage
from parishkit.stewardship.responses.models import ProposedChange
from parishkit.stewardship.workflows.models import MinistryRequest
from parishkit.stewardship.workflows.roster import roster_marks

from .test_census_resolution_postgresql import prepared, resolve
from .test_census_review_postgresql import review
from .test_ministry_followup_postgresql import edit, requests
from .test_ministry_reports_postgresql import setup
from .test_ministry_responses_postgresql import ministry_source
from .test_roster_entries_postgresql import resolved, tick
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def census(row):
    """The stored proposal's placement."""
    row = ProposedChange.objects.get(pk=row.pk)
    return pipeline.census_placement(
        decision=row.decision,
        execution=row.execution,
        handling=row.handling,
        entity_kind=row.entity_kind,
        admin_value_set=row.admin_value_set,
    )


def ministry(row):
    """The stored request's placement, its tick read as the pages read it."""
    row = MinistryRequest.objects.get(pk=row.pk)
    marks = roster_marks([row.pk])
    return pipeline.ministry_placement(
        state=row.state,
        outcome=row.outcome,
        source_resolved=row.resolution_source_id is not None,
        roster_entered=bool(marks.get(str(row.pk), {}).get("entered")),
    )


def test_census_rows_map_through_review_and_resolution(response_service):
    """Every stored proposal maps; report-only ones are outside the census
    stream; review and resolution move each to the spec's stage."""
    harness, admin, staff, home, name = prepared(response_service)
    rows = list(ProposedChange.objects.all())
    assert rows
    for row in rows:
        placement = census(row)
        if row.handling == "report-only":
            assert placement is None
        else:
            assert placement == Placement(Stage.TO_REVIEW)
    # The pipeline's By hand rule agrees with the stored handling.
    assert pipeline.census_by_hand(home.handling, home.entity_kind)
    assert not pipeline.census_by_hand(name.handling, name.entity_kind)

    review(harness, admin, name, "approved")
    assert census(name) == Placement(Stage.READY_TO_UPDATE, Decision.ACCEPTED)

    resolve(harness, staff, home, "ignored", note="Duplicate")
    assert census(home) == Placement(Stage.IGNORED, Decision.IGNORED)
    home.refresh_from_db()
    resolve(harness, admin, home, "reopened", note="Not a duplicate")
    assert census(home) == Placement(Stage.TO_REVIEW)
    # Entered by hand on a To review row accepts and marks in one step.
    home.refresh_from_db()
    resolve(harness, staff, home, "entered")
    assert census(home) == Placement(Stage.ENTERED_BY_HAND, Decision.ACCEPTED)


def test_ministry_requests_map_through_outcome_and_tick(response_service):
    """Open, resolved, ticked and declined requests reach the spec's stages."""
    harness, admin, join, leave = resolved(response_service)
    assert ministry(leave) == Placement(Stage.TO_REVIEW)
    assert ministry(join) == Placement(
        Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED, outcome="joined"
    )
    tick(harness, admin, join, True)
    assert ministry(join) == Placement(
        Stage.ENTERED_BY_HAND, Decision.ACCEPTED, outcome="joined"
    )
    tick(harness, admin, join, False)
    assert ministry(join).stage is Stage.TO_ENTER_BY_HAND
    edit(harness, admin, leave, state="resolved", outcome="declined")
    assert ministry(leave) == Placement(
        Stage.IGNORED, Decision.IGNORED, outcome="declined"
    )


def test_ministry_request_resolved_by_the_roster_is_confirmed(response_service):
    """A refresh whose roster shows the join resolves it: Confirmed, with no
    reviewer; the untouched leave stays To review."""
    harness = setup(response_service)
    data = ministry_source()
    data.ministry_type_memberships[9]["membership"] = [
        {
            "memberId": 3,
            "ministryRoleId": 1,
            "ministryRoleName": "Participant",
            "startDate": "2026-01-01",
            "endDate": None,
        }
    ]
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    join, leave = requests()
    assert join.resolution_source_id is not None
    assert ministry(join) == Placement(Stage.CONFIRMED, outcome="joined")
    assert ministry(leave) == Placement(Stage.TO_REVIEW)
