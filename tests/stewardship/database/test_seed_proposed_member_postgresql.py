"""The seeder's proposed new Member goes through the real submission path (#498).

``build_answers`` maps a timeline's ``proposed_member`` census edit onto the
form's own projection; the Family form owner must accept it and record the
new Member as a ``ProposedChange`` for the Chairperson review, exactly as a
browser submission would. The payload shape itself is tested without a
database in ``test_seed_web.py``.
"""

import pytest

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.local.seed_web import build_answers
from parishkit.stewardship.responses.baselines import issue_baseline
from parishkit.stewardship.responses.models import ProposedChange, Submission
from parishkit.stewardship.responses.submission import submit_family
from parishkit.stewardship.workflows.models import MinistryRequest

from .response_builders import activate_response_service
from .test_ministry_responses_postgresql import start
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def seed_submit(harness, answers):
    """Open the form and submit the seeder's answers, as ``SeedWeb`` does."""
    with web_login():
        with work_transaction():
            form = issue_baseline(harness.request, harness.service)
        payload = build_answers(form, answers)
        with work_transaction():
            result = submit_family(
                harness.request,
                harness.service,
                baseline_id=form.baseline.pk,
                payload=payload,
            )
    assert result.refreshed is None
    return payload


def test_the_seeders_proposed_member_is_accepted_and_reviewed(live_response_service):
    harness = live_response_service
    payload = seed_submit(harness, {"census_edit": "proposed_member"})
    ((identity, member),) = payload["proposed_members"].items()
    assert Submission.objects.count() == 1
    change = ProposedChange.objects.get(entity_kind="proposed_member")
    assert (change.entity_key, change.field) == (identity, "new_member")
    assert change.submitted_value["first_name"] == member["first_name"]
    assert change.submitted_value["last_name"] == member["last_name"]


def test_the_proposed_member_joins_the_first_offered_ministry(response_service):
    """With Ministries offered, the new Member's join is accepted too."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    payload = seed_submit(harness, {"census_edit": "proposed_member"})
    ((identity, join),) = payload["ministries"]["proposed_members"].items()
    assert join["join"]
    assert set(payload["proposed_members"]) == {identity}
    assert ProposedChange.objects.filter(
        entity_kind="proposed_member", entity_key=identity, field="new_member"
    ).exists()
    # The join is a Ministry request for the new Member, for the leaders.
    (request,) = MinistryRequest.objects.filter(
        entity_kind="proposed_member", entity_key=identity
    )
    assert (request.action, request.ministry_duid) == ("join", join["join"][0])
