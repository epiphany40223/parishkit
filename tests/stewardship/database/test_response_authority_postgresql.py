"""Real runtime roles cannot manufacture review outcomes or unfenced comparisons."""

import json

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.models import ProposedChange, Submission
from parishkit.stewardship.source.models import SourceSnapshotPin

from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import form_and_answers, submit
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "override",
    [
        {"decision": "approved"},
        {"decision": "ignored"},
        {"admin_value_set": True, "admin_value": "Invented review"},
        {"execution": "published"},
        {"execution": "resolved_external"},
        {"baseline_available": False},
        {"baseline_value": "Requested name"},
        {"current_available": False},
        {"current_value": "Forged source"},
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


def test_live_worker_cannot_use_noncurrent_promoted_source(
    live_response_service, monkeypatch
):
    """A live source lease authorizes exactly its current snapshot, not any pin."""
    from parishkit.stewardship.responses import reconciliation

    harness = live_response_service
    earlier, claim = prepare(response_source())
    promote(earlier, claim, harness.campaign, harness.rings)
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    answers["members"]["3"]["first_name"] = "Requested name"
    response = submit(harness, form, answers).submission
    data = response_source()
    data.members[3]["firstName"] = "Updated source"
    current, claim = prepare(data)
    original = reconciliation.reconcile_proposals
    checked = []

    def probe(snapshot, corpus, *, campaign_id):
        """Probe exact SQL while the real promotion and both fences are live."""
        result = original(snapshot, corpus, campaign_id=campaign_id)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_response_source_owner_v1(%s), "
                "stewardship_response_source_owner_v1(%s), "
                "stewardship_response_source_owner_v1(NULL)",
                [current.pk, earlier.pk],
            )
            assert cursor.fetchone() == (True, False, False)
        with (
            pytest.raises(IntegrityError, match="Worker source protection"),
            transaction.atomic(),
        ):
            SourceSnapshotPin.objects.create(
                snapshot_id=harness.snapshot.pk,
                parent_kind="submission",
                parent_id=response.pk,
            )
        # The old reviewed input already has a permanent pin. Its protection
        # alone must not grant authority to rebase back to that obsolete source.
        with (
            pytest.raises(IntegrityError, match="fenced protected source"),
            transaction.atomic(),
        ):
            ProposedChange.objects.filter(submission=response).update(
                current_source_id=earlier.pk, version=F("version") + 1
            )
        checked.append(True)
        return result

    monkeypatch.setattr(reconciliation, "reconcile_proposals", probe)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(current, claim, harness.campaign, harness.rings)
    assert checked == [True]


@pytest.mark.parametrize(
    "variant",
    ["ordinary", "multiple_email", "null_email", "missing_email", "missing_middle"],
)
def test_sql_source_projection_matches_issued_field_registry(response_service, variant):
    """The independent insert guard reconstructs every supported field exactly."""
    harness = response_service
    data = response_source()
    if variant == "multiple_email":
        data.members[3]["emailAddress"] = "z@example.org; a@example.org"
    elif variant == "null_email":
        data.members[3]["emailAddress"] = None
    elif variant == "missing_email":
        data.members[3].pop("emailAddress")
    elif variant == "missing_middle":
        data.member_contactinfos[3].pop("middleName")
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    with web_login():
        form, answers = form_and_answers(harness)
        for field in form.inputs.fields:
            if field.entity != "member":
                continue
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT stewardship_response_field_source_v1(%s,%s,%s,%s)",
                    [
                        form.baseline.source_id,
                        form.baseline.family_id,
                        str(field.identity),
                        field.field,
                    ],
                )
                assert json.loads(cursor.fetchone()[0]) == {
                    "available": field.source.available,
                    "value": field.source.value,
                }
            answers["members"][str(field.identity)][field.field] = {
                "first_name": "Updated first",
                "middle_name": "Updated middle",
                "last_name": "Updated last",
                "email": "new@example.org",
            }[field.field]
        response = submit(harness, form, answers).submission
        assert ProposedChange.objects.filter(submission=response).count() == 4


def test_terminal_old_intent_starts_from_current_source(live_response_service):
    """A new request cannot inherit an obsolete cancelled comparison baseline."""
    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    answers["members"]["3"]["first_name"] = "Requested name"
    first = submit(harness, form, answers).submission
    data = response_source()
    data.members[3]["firstName"] = "Updated source"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    with work_transaction():
        ProposedChange.objects.filter(submission=first).update(
            execution="cancelled", version=F("version") + 1
        )
    harness, form, answers, _ = revisit(harness)
    assert answers["members"]["3"]["first_name"] == "Updated source"
    answers["members"]["3"]["first_name"] = "Requested name"
    with web_login():
        second = submit(harness, form, answers).submission
    proposal = ProposedChange.objects.get(submission=second)
    assert proposal.baseline_value == "Updated source"
    assert proposal.decision == "unreviewed" and proposal.execution == "pending"
