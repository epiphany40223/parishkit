"""Real baseline issuance, source pins, session binding and transactional guards."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.responses.baselines import (
    FamilyAdmissionDenied,
    RehearsalAcknowledgmentRequired,
    end_baseline,
    issue_baseline,
)
from parishkit.stewardship.responses.models import FamilyFormBaseline, Submission
from parishkit.stewardship.source.models import SourceSnapshotPin

from .test_family_auth_postgresql import login

pytestmark = pytest.mark.django_db(transaction=True)


def issue(harness, **kwargs):
    """Call the same request-scoped service the upcoming public route will use."""
    return issue_baseline(harness.request, harness.service, **kwargs)


def test_rehearsal_requires_entry_ack_before_any_form_metadata(response_service):
    with pytest.raises(RehearsalAcknowledgmentRequired):
        issue(response_service)
    assert not FamilyFormBaseline.objects.exists()
    assert not SourceSnapshotPin.objects.filter(parent_kind="form_baseline").exists()
    assert not Submission.objects.exists()


def test_issued_baseline_is_answer_free_and_exactly_pinned(response_service):
    form = issue(response_service, testing_acknowledged=True)
    baseline = form.baseline
    assert baseline.source_id == response_service.snapshot.pk
    assert baseline.projection_digest == form.inputs.projection_digest
    assert baseline.family_session_id == response_service.request.family_session.pk
    assert baseline.expires_at == response_service.request.family_session.expires_at
    assert baseline.mode == "test" and baseline.rehearsal_epoch_id
    assert baseline.prior_submission_id is None
    assert form.inputs.member_duids == (3,)
    assert not Submission.objects.exists()
    assert not any(
        field.name in {"answers", "values", "draft", "payload"}
        for field in baseline._meta.fields
    )
    pin = SourceSnapshotPin.objects.get(
        parent_kind="form_baseline", parent_id=baseline.pk
    )
    assert (
        pin.snapshot_id == baseline.source_id and pin.expires_at == baseline.expires_at
    )


def test_refresh_replaces_metadata_and_pin_not_answers(response_service):
    first = issue(response_service, testing_acknowledged=True).baseline
    second = issue(response_service).baseline
    first.refresh_from_db()
    assert first.state == "replaced" and first.ended_at is not None
    assert second.state == "open" and second.pk != first.pk
    assert first.projection_digest == second.projection_digest
    assert set(
        SourceSnapshotPin.objects.filter(parent_kind="form_baseline").values_list(
            "parent_id", flat=True
        )
    ) == {second.pk}


def test_different_session_requires_its_own_ack(response_service):
    first = issue(response_service, testing_acknowledged=True).baseline
    _, response = login(response_service.code)
    with pytest.raises(RehearsalAcknowledgmentRequired):
        issue_baseline(response.wsgi_request, response_service.service)
    assert FamilyFormBaseline.objects.count() == 1
    first.refresh_from_db()
    assert first.state == "open"


def test_cancel_releases_only_own_pin(response_service):
    baseline = issue(response_service, testing_acknowledged=True).baseline
    with work_transaction():
        assert end_baseline(baseline, state="cancelled")
        assert not end_baseline(baseline, state="cancelled")
    baseline.refresh_from_db()
    assert baseline.state == "cancelled"
    assert not SourceSnapshotPin.objects.filter(
        parent_kind="form_baseline", parent_id=baseline.pk
    ).exists()
    assert not Submission.objects.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("family_session_id", uuid4()),
        ("projection_digest", "f" * 64),
        ("expires_at", None),
    ],
)
def test_sql_rejects_rebinding_even_with_version_bump(response_service, field, value):
    baseline = issue(response_service, testing_acknowledged=True).baseline
    if field == "expires_at":
        value = baseline.expires_at + timedelta(hours=1)
    with pytest.raises(IntegrityError, match="immutable"), work_transaction():
        FamilyFormBaseline.objects.filter(pk=baseline.pk).update(
            **{field: value},
            state="cancelled",
            ended_at=baseline.created_at,
            version=F("version") + 1,
        )
    baseline.refresh_from_db()
    assert baseline.state == "open"


def test_missing_pin_rolls_back_entire_issuance(response_service, monkeypatch):
    from parishkit.stewardship.responses import baselines

    monkeypatch.setattr(baselines, "pin_snapshot", lambda *args, **kwargs: None)
    with pytest.raises(IntegrityError, match="source protection"):
        issue(response_service, testing_acknowledged=True)
    assert not FamilyFormBaseline.objects.exists()
    assert not Submission.objects.exists()


def test_submitted_state_requires_atomic_submission(response_service):
    baseline = issue(response_service, testing_acknowledged=True).baseline
    with pytest.raises(IntegrityError, match="immutable response"), work_transaction():
        end_baseline(baseline, state="submitted")
    baseline.refresh_from_db()
    assert baseline.state == "open"
    assert SourceSnapshotPin.objects.filter(parent_id=baseline.pk).exists()


def test_ended_state_requires_atomic_unpin(response_service):
    baseline = issue(response_service, testing_acknowledged=True).baseline
    with pytest.raises(IntegrityError, match="expiring protection"), work_transaction():
        FamilyFormBaseline.objects.filter(pk=baseline.pk).update(
            state="cancelled", ended_at=baseline.created_at, version=F("version") + 1
        )
    baseline.refresh_from_db()
    assert baseline.state == "open"


def test_metadata_writes_require_common_order(response_service):
    baseline = issue(response_service, testing_acknowledged=True).baseline
    with (
        pytest.raises(IntegrityError, match="ordered admission"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "DELETE FROM stewardship_family_form_baseline WHERE id=%s", [baseline.pk]
        )


def test_lost_session_cannot_issue_private_inputs(response_service):
    response_service.client.post(
        "/family/logout",
        {
            "csrfmiddlewaretoken": response_service.client.cookies[
                "pk_family_csrf"
            ].value
        },
    )
    with pytest.raises(FamilyAdmissionDenied):
        issue(response_service, testing_acknowledged=True)
    assert not FamilyFormBaseline.objects.exists()


def test_pin_side_cannot_remove_active_protection(response_service):
    baseline = issue(response_service, testing_acknowledged=True).baseline
    with pytest.raises(IntegrityError, match="source protection"), work_transaction():
        SourceSnapshotPin.objects.filter(
            parent_kind="form_baseline", parent_id=baseline.pk
        ).delete()
    assert SourceSnapshotPin.objects.filter(parent_id=baseline.pk).exists()


def test_logout_cancels_unfinished_baseline_and_pin(response_service):
    baseline = issue(response_service, testing_acknowledged=True).baseline
    response = response_service.client.post(
        "/family/logout",
        {
            "csrfmiddlewaretoken": response_service.client.cookies[
                "pk_family_csrf"
            ].value
        },
    )
    assert response.status_code == 302
    baseline.refresh_from_db()
    assert baseline.state == "cancelled"
    assert not SourceSnapshotPin.objects.filter(parent_id=baseline.pk).exists()


def test_session_housekeeping_releases_answer_free_metadata(response_service):
    from parishkit.stewardship.accounts.sessions import (
        cleanup_family_sessions,
        database_now,
        revoke_family_sessions,
    )

    baseline = issue(response_service, testing_acknowledged=True).baseline
    with work_transaction():
        revoke_family_sessions(
            [response_service.request.family_session], now=database_now()
        )
    assert cleanup_family_sessions() == 1
    baseline.refresh_from_db()
    assert baseline.state == "cancelled"
    assert not SourceSnapshotPin.objects.filter(parent_id=baseline.pk).exists()


def test_real_web_grants_issue_replace_and_cancel_baseline(response_service):
    """Exercise closed operational grants, not just the privileged schema owner."""
    from .test_runtime_auth_grants_postgresql import web_login

    with web_login():
        first = issue(response_service, testing_acknowledged=True).baseline
        second = issue(response_service).baseline
        with work_transaction():
            assert end_baseline(second, state="cancelled")
        first.refresh_from_db()
        assert first.state == "replaced"
