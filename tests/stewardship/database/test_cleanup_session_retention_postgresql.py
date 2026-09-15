"""Family logout stays usable while cleanup exclusively owns Testing detail."""

import pytest

from parishkit.stewardship.accounts.sessions import cleanup_family_sessions
from parishkit.stewardship.campaigns.credential_models import FamilySession
from parishkit.stewardship.responses.models import FamilyFormBaseline

from .test_cleanup_tasks_postgresql import queued, run
from .test_response_submission_postgresql import form_and_answers

pytestmark = pytest.mark.django_db(transaction=True)


def test_logout_preserves_sealed_cleanup_detail(response_service):
    """Logout revokes access but leaves the sealed baseline/pin for its worker."""
    form, _ = form_and_answers(response_service)
    status = queued(response_service)
    client = response_service.client
    result = client.post(
        "/family/logout",
        {"csrfmiddlewaretoken": client.cookies["csrftoken"].value},
    )
    assert result.status_code == 302
    session = FamilySession.objects.get(pk=form.baseline.family_session_id)
    assert session.revoked_at is not None
    assert FamilyFormBaseline.objects.get(pk=form.baseline.pk).state == "open"
    assert cleanup_family_sessions() == 0
    assert run(status)
    assert not FamilySession.objects.filter(pk=session.pk).exists()
