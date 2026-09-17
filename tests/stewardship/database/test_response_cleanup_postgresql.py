"""Invalidated-epoch retention is bounded, irreversible and isolated from live data."""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection

from parishkit.stewardship.campaigns.credential_models import (
    RehearsalCodeReservation,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.rehearsals import (
    cleanup_rehearsal,
    invalidate_rehearsal,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.responses.baselines import issue_baseline
from parishkit.stewardship.responses.models import (
    FamilyFormBaseline,
    ProposedChange,
    Submission,
    SubmissionReceiptOccurrence,
)
from parishkit.stewardship.source.models import SourceSnapshotPin
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import command, prepared_tokens
from .response_builders import response_source
from .test_family_auth_postgresql import login
from .test_response_submission_postgresql import form_and_answers, submit
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def no_receipt_response(response_service):
    """Legacy bounded cleanup handles responses without concrete mail deliveries.

    Receipt-bearing submissions use the journaled cleanup-worker tests, which
    additionally cover drained tasks, outbox history and immutable renders.
    """
    data = response_source()
    data.members[3]["emailAddress"] = ""
    snapshot, claim = prepare(data)
    promote(snapshot, claim, response_service.campaign, response_service.rings)
    return response_service


def test_active_test_answers_cannot_be_purged(response_service):
    form, answers = form_and_answers(response_service)
    row = submit(response_service, form, answers).submission
    with pytest.raises(StorageInvariantError, match="active"):
        cleanup_rehearsal(row.rehearsal_epoch_id)
    with (
        pytest.raises(IntegrityError, match="immutable"),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_submission WHERE id=%s", [row.pk])
    assert Submission.objects.filter(pk=row.pk).exists()


def test_new_rehearsal_epoch_does_not_reuse_retained_response_versions(
    response_service,
):
    """Cancelled go-live can reopen rehearsal before old cleanup finishes."""
    from parishkit.stewardship.campaigns.lifecycle import CampaignWorkKind
    from parishkit.stewardship.campaigns.rehearsals import (
        code_context,
        prepare_rehearsals,
        release_rehearsal_gate,
    )

    harness = response_service
    form, answers = form_and_answers(harness)
    old = submit(harness, form, answers).submission
    invalidate_rehearsal(campaign_id=harness.campaign.pk, admit=lambda *args: True)
    release_rehearsal_gate(campaign_id=harness.campaign.pk, admit=lambda *args: True)
    credentials = prepare_rehearsals(
        campaign_id=harness.campaign.pk,
        family_ids=[old.family_id],
        general=harness.rings.general,
        mac=harness.rings.mac,
        public=harness.rings.public,
        purpose=CampaignWorkKind.REHEARSAL,
        admit=lambda *args: True,
    )
    credential = RehearsalCredential.objects.get(pk=credentials[old.family_id])
    code = harness.rings.general.decrypt(
        credential.code_ciphertext, context=code_context(credential.pk)
    ).decode()
    client, response = login(code)
    harness = replace(harness, client=client, request=response.wsgi_request, code=code)
    form, answers = form_and_answers(harness)
    new = submit(harness, form, answers).submission
    assert new.family_version == old.family_version + 1
    assert new.rehearsal_epoch_id != old.rehearsal_epoch_id
    assert new.prior_submission_id is None
    assert Submission.objects.count() == 2


def test_bounded_cleanup_removes_chains_metadata_and_pins_but_keeps_reservations(
    no_receipt_response,
):
    harness = no_receipt_response
    for index in range(3):
        if index:
            client, response = login(harness.code)
            harness = replace(harness, client=client, request=response.wsgi_request)
        form, answers = form_and_answers(harness)
        answers["members"]["3"]["first_name"] = "Same test update"
        submit(harness, form, answers)
    client, response = login(harness.code)
    issue_baseline(response.wsgi_request, harness.service, testing_acknowledged=True)
    epoch = invalidate_rehearsal(
        campaign_id=harness.campaign.pk, admit=lambda *args: True
    )
    reservations = list(RehearsalCodeReservation.objects.values_list("pk", flat=True))
    counts = []
    for _ in range(30):
        count = cleanup_rehearsal(epoch, batch_size=1)
        counts.append(count)
        if not count:
            break
    assert counts[-1] == 0 and len(counts) > 2
    assert not Submission.objects.exists()
    assert not ProposedChange.objects.exists()
    assert not SubmissionReceiptOccurrence.objects.exists()
    assert not FamilyFormBaseline.objects.exists()
    assert not SourceSnapshotPin.objects.filter(
        parent_kind__in=["submission", "form_baseline"]
    ).exists()
    assert (
        list(RehearsalCodeReservation.objects.values_list("pk", flat=True))
        == reservations
    )


def test_activation_rejects_unremoved_test_detail_even_after_credentials_are_gone(
    no_receipt_response,
    monkeypatch,
):
    harness = no_receipt_response
    form, answers = form_and_answers(harness)
    row = submit(harness, form, answers).submission
    epoch = invalidate_rehearsal(
        campaign_id=harness.campaign.pk, admit=lambda *args: True
    )
    from parishkit.stewardship.responses import cleanup

    # Simulate an interrupted response-cleanup category while the existing
    # credential/session owner completes. Activation must independently inspect
    # the remaining response detail, not trust the empty credential inventory.
    with monkeypatch.context() as patch:
        patch.setattr(cleanup, "cleanup_test_responses", lambda *args, **kwargs: 0)
        cleanup_rehearsal(epoch)
    assert not RehearsalCredential.objects.exists()
    actor = uuid4()
    generation = prepared_tokens(harness.campaign, actor)
    with pytest.raises(IntegrityError, match="rehearsal cleanup"):
        command(
            harness.campaign, actor, Action.ACTIVATE, token_generation_id=generation
        )
    assert Submission.objects.filter(pk=row.pk).exists()
    while cleanup_rehearsal(epoch, batch_size=1):
        pass
    command(harness.campaign, actor, Action.ACTIVATE, token_generation_id=generation)


def test_live_response_deletion_is_never_authorized_by_rehearsal_cleanup(
    live_response_service,
):
    form, answers = form_and_answers(live_response_service)
    answers["testing_acknowledged"] = False
    row = submit(live_response_service, form, answers).submission
    with (
        pytest.raises(IntegrityError, match="immutable"),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_submission WHERE id=%s", [row.pk])
    assert Submission.objects.filter(pk=row.pk).exists()
