"""Real snapshots prove that relevant values, not global versions, control review."""

from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.responses.baselines import (
    FamilyAdmissionDenied,
    admitted_family,
    issue_baseline,
)
from parishkit.stewardship.responses.models import Submission
from parishkit.stewardship.responses.validation import (
    BaselineUnavailable,
    validate_baseline,
)

from .response_builders import response_source as source
from .test_family_auth_postgresql import login
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def validate(harness, identifier, *, request=None):
    """Use current authorization in the same ordered transaction as reconstruction."""
    with work_transaction():
        _, campaign, family, session = admitted_family(
            request or harness.request, harness.service
        )
        return validate_baseline(
            identifier, family=family, session=session, campaign=campaign
        )


@pytest.mark.parametrize(
    "change,stale",
    [
        ("same", False),
        ("other_family", False),
        ("canonical", False),
        ("name", True),
        ("new_member", True),
        ("remove_member", True),
        ("birth_date", True),
    ],
)
def test_promotions_compare_exact_form_dependencies(response_service, change, stale):
    data = source()
    if change == "remove_member":
        # Keep the Family eligible after removing one Member. Losing the final
        # active Member is a denial, not permission to show a refreshed form.
        data.members[8] = {**data.members[3], "memberDUID": 8, "memberType": "Spouse"}
        snapshot, claim = prepare(data)
        promote(snapshot, claim, response_service.campaign, response_service.rings)
    original = issue_baseline(
        response_service.request, response_service.service, testing_acknowledged=True
    )
    if change == "other_family":
        data.families[2]["lastName"] = "Different unrelated Family"
    elif change == "canonical":
        data.members[3]["emailAddress"] = " VALID@EXAMPLE.ORG "
    elif change == "name":
        data.members[3]["firstName"] = "Updated"
    elif change == "new_member":
        data.members[8] = {**data.members[3], "memberDUID": 8, "memberType": "Child"}
    elif change == "remove_member":
        data.members[3]["memberStatus"] = "Inactive"
    elif change == "birth_date":
        data.members[3]["birthdate"] = "1961-01-01"
    snapshot, claim = prepare(data)
    current = promote(
        snapshot, claim, response_service.campaign, response_service.rings
    )
    assert current.pk != original.baseline.source_id
    result = validate(response_service, original.baseline.pk)
    assert result.review_required is stale
    assert result.validation_snapshot_id == current.pk
    assert result.baseline.source_id == original.baseline.source_id
    assert result.reviewed.projection_digest == original.inputs.projection_digest
    assert not Submission.objects.exists()


def test_promotion_losing_family_eligibility_never_returns_refreshed_data(
    response_service,
):
    form = issue_baseline(
        response_service.request, response_service.service, testing_acknowledged=True
    )
    data = source()
    data.members[3]["memberStatus"] = "Inactive"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, response_service.campaign, response_service.rings)
    with pytest.raises(FamilyAdmissionDenied):
        validate(response_service, form.baseline.pk)
    assert not Submission.objects.exists()


def test_baseline_reference_cannot_cross_family_sessions(response_service):
    form = issue_baseline(
        response_service.request, response_service.service, testing_acknowledged=True
    )
    _, response = login(response_service.code)
    for identifier in (form.baseline.pk, uuid4(), str(form.baseline.pk)):
        with pytest.raises(BaselineUnavailable, match="fresh authorized"):
            validate(response_service, identifier, request=response.wsgi_request)


def test_replaced_baseline_cannot_be_submitted(response_service):
    old = issue_baseline(
        response_service.request, response_service.service, testing_acknowledged=True
    )
    new = issue_baseline(response_service.request, response_service.service)
    with pytest.raises(BaselineUnavailable):
        validate(response_service, old.baseline.pk)
    assert not validate(response_service, new.baseline.pk).review_required


def test_reconstruction_works_under_restricted_web_login(response_service):
    from .test_runtime_auth_grants_postgresql import web_login

    with web_login():
        form = issue_baseline(
            response_service.request,
            response_service.service,
            testing_acknowledged=True,
        )
        assert not validate(response_service, form.baseline.pk).review_required
