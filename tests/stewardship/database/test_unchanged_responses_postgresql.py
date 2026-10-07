"""Open requests let an identical quick update skip promotion only when
reconciliation would change none of them (#630).

``proposals_current`` and ``ministry_requests_current`` share their decision
with ``reconcile_proposals`` and ``reconcile_ministry_requests``. A new
submission compared against the current snapshot, and the rows a promotion
itself reconciled, compare equal; a request the corpus now answers does not.
"""

from dataclasses import replace

import pytest

from parishkit.stewardship.responses.ministry_reconciliation import (
    ministry_requests_current,
)
from parishkit.stewardship.responses.models import ProposedChange
from parishkit.stewardship.responses.reconciliation import proposals_current
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.snapshots import reconstruct_snapshot
from parishkit.stewardship.workflows.models import MinistryRequest

from .response_builders import response_source
from .test_ministry_responses_postgresql import (
    ministry_source,
    respond,
    start,
)
from .test_response_http_postgresql import answers_for
from .test_response_submission_postgresql import form_and_answers, submit
from .test_source_families_postgresql import TODAY, prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def corpus_of(data):
    """The corpus a promotion of ``data`` would hold, without promoting it."""
    data = replace(data, organization_id=12345)
    for family in data.families.values():
        if family.get("registeredOrganizationID") == 5:
            family["registeredOrganizationID"] = 12345
    return normalize_core(data, as_of=TODAY)


def test_submission_against_the_current_snapshot_is_current(response_service):
    """A new proposal compares equal; a later source value does not."""
    harness = response_service
    form, answers = form_and_answers(harness)
    answers["members"]["3"]["first_name"] = "Requested name"
    submit(harness, form, answers)
    assert ProposedChange.objects.filter(execution="pending").exists()
    campaign = harness.campaign.pk
    assert proposals_current(reconstruct_snapshot(), campaign_id=campaign)
    data = response_source()
    data.members[3]["firstName"] = "Updated source"
    assert not proposals_current(corpus_of(data), campaign_id=campaign)
    # Once a promotion has reconciled that value, the rows it wrote compare
    # equal: its own writes never force another promotion.
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    assert ProposedChange.objects.get().current_value == "Updated source"
    assert proposals_current(reconstruct_snapshot(), campaign_id=campaign)


def test_ministry_request_the_corpus_answers_is_not_current(response_service):
    """A join the roster now shows is resolved by promotion, so it promotes."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"]["join"] = [9]
    respond(harness, form, answers)
    campaign = harness.campaign.pk
    assert MinistryRequest.objects.get().state != "resolved"
    assert ministry_requests_current(reconstruct_snapshot(), campaign_id=campaign)
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
    assert not ministry_requests_current(corpus_of(data), campaign_id=campaign)
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    assert MinistryRequest.objects.get().state == "resolved"
    assert ministry_requests_current(reconstruct_snapshot(), campaign_id=campaign)
