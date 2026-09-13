"""Real runtime roles cannot manufacture review outcomes or unfenced comparisons."""

import pytest
from django.db import IntegrityError
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.models import ProposedChange, Submission
from parishkit.stewardship.source.models import SourceSnapshotPin

from .test_background_grants_postgresql import task_login
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import form_and_answers, submit
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "override",
    [
        {"decision": "approved"},
        {"decision": "ignored"},
        {"admin_value_set": True, "admin_value": "Invented review"},
        {"execution": "published"},
        {"execution": "resolved_external"},
    ],
)
def test_web_cannot_mint_review_state_on_insert(
    response_service, monkeypatch, override
):
    """An INSERT grant accepts Family intent, not a shortcut around later review."""
    form, answers = form_and_answers(response_service)
    answers["members"]["3"]["first_name"] = "Requested name"
    manager = ProposedChange.objects
    original = manager.create

    def altered(**values):
        """Inject forged service output just before the real constrained INSERT."""
        return original(**(values | override))

    monkeypatch.setattr(manager, "create", altered)
    with web_login(), pytest.raises(IntegrityError):
        submit(response_service, form, answers)
    assert not Submission.objects.exists()
    assert not ProposedChange.objects.exists()


@pytest.mark.parametrize(
    "execution", ["published", "resolved_external", "cancelled", "resolved_upstream"]
)
def test_web_cannot_change_outcome_without_new_response(response_service, execution):
    """Even the common lock cannot turn a web login into the publish owner."""
    form, answers = form_and_answers(response_service)
    answers["members"]["3"]["first_name"] = "Requested name"
    submit(response_service, form, answers)
    proposal = ProposedChange.objects.get()
    with web_login(), pytest.raises(IntegrityError), work_transaction():
        ProposedChange.objects.filter(pk=proposal.pk).update(
            execution=execution, version=F("version") + 1
        )
    proposal.refresh_from_db()
    assert proposal.execution == "pending"


def test_same_intent_can_carry_verified_review_decision(live_response_service):
    """A revisit carries, but cannot invent, a parish reviewer decision."""
    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    answers["members"]["3"]["first_name"] = "Requested name"
    first = submit(harness, form, answers).submission
    with work_transaction():
        ProposedChange.objects.filter(submission=first).update(
            decision="approved",
            admin_value_set=True,
            admin_value="Reviewed name",
            version=F("version") + 1,
        )
    harness, form, answers, _ = revisit(harness)
    with web_login():
        second = submit(harness, form, answers).submission
    carried = ProposedChange.objects.get(submission=second)
    assert carried.decision == "approved" and carried.admin_value_set
    assert carried.admin_value == "Reviewed name" and carried.execution == "pending"
    assert second.answers["members"]["3"]["first_name"] == "Requested name"


def test_worker_cannot_reconcile_without_live_source_owner(response_service):
    """Correct role, common lock and pin still do not replace both live fences."""
    form, answers = form_and_answers(response_service)
    answers["members"]["3"]["first_name"] = "Requested name"
    response = submit(response_service, form, answers).submission
    proposal = ProposedChange.objects.get(submission=response)
    with task_login(ServiceRole.WORKER, exact=True):
        with pytest.raises(IntegrityError), work_transaction():
            ProposedChange.objects.filter(pk=proposal.pk).update(
                current_value="Forged source",
                execution="resolved_upstream",
                version=F("version") + 1,
            )
        with pytest.raises(IntegrityError), work_transaction():
            SourceSnapshotPin.objects.filter(parent_id=response.pk).delete()
    proposal.refresh_from_db()
    assert proposal.execution == "pending" and proposal.current_value == "Member"
    assert SourceSnapshotPin.objects.filter(parent_id=response.pk).exists()


def test_web_cannot_unpin_an_open_form(response_service):
    """The deferred pin-side guard already protects another active tab's input."""
    form, _ = form_and_answers(response_service)
    with (
        web_login(),
        pytest.raises(IntegrityError, match="source protection"),
        work_transaction(),
    ):
        SourceSnapshotPin.objects.filter(parent_id=form.baseline.pk).delete()
    assert SourceSnapshotPin.objects.filter(parent_id=form.baseline.pk).exists()
