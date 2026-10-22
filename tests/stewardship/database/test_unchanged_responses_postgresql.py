"""Open requests let an identical quick update skip promotion only when
reconciliation would change none of them (#630).

``proposals_current`` shares its decision with ``reconcile_proposals``. A
new submission compared against the current snapshot, and the rows a
promotion itself reconciled, compare equal. A refresh no longer resolves a
Ministry request the corpus now answers (strict phase order, #1890), so
there is nothing to compare for one.
"""

from dataclasses import replace

import pytest

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


def test_a_ministry_request_the_corpus_answers_stays_open(response_service):
    """A join the roster now shows keeps its stage after promotion (#1890):
    it is decided and ticked first, so the refresh resolves nothing."""
    harness = response_service
    form = start(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"]["join"] = [9]
    respond(harness, form, answers)
    before = MinistryRequest.objects.get()
    assert before.state != "resolved"
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
    after = MinistryRequest.objects.get()
    assert (after.state, after.version, after.resolution_source_id) == (
        before.state,
        before.version,
        None,
    )
