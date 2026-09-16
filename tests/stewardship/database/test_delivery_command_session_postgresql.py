"""Current browser-session ownership survives the interval around domain effects."""

# ruff: noqa: F811 -- pytest injects the imported fixture by name.

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db.models import F

from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import delivery_views, family_mail_tasks
from parishkit.stewardship.jobs.delivery_resolution_models import DeliveryResolution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.recipient_models import RecipientRefusalResolution
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from .auth_builders import signed_in
from .campaign_builders import campaign_clock
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_delivery_resolution_postgresql import failed_delivery
from .test_family_mail_preparation_postgresql import allocate, family_mail  # noqa: F401
from .test_recipient_suppressions_postgresql import refused, remember
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(params=["resolution", "clearance", "preparation"])
def form(family_mail, google, request):
    """Build genuine command targets, returning only the browser intent receipt."""
    identifier = uuid4()
    values = {"command_id": str(identifier)}
    if request.param == "clearance":
        family_mail = activate_response_service(family_mail)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        if request.param == "resolution":
            message = failed_delivery(family_mail)
            path = f"/admin/deliveries/{message.pk}/resolve"
            values |= dict(
                expected_version=str(message.version),
                action="note",
                note="Private evidence",
            )
            owner, function = delivery_views, "resolve_delivery"
            receipts = DeliveryResolution.objects.filter(pk=identifier)
        elif request.param == "clearance":
            refusal = remember(refused(family_mail))
            source = SourceCurrent.objects.get()
            path = f"/admin/deliveries/refusals/{refusal.pk}/clear"
            values |= dict(
                source_snapshot_id=str(source.snapshot_id),
                source_generation=str(source.generation),
                note="Private evidence",
                verified="yes",
            )
            owner, function = delivery_views, "clear_recipient_refusal"
            receipts = RecipientRefusalResolution.objects.filter(pk=identifier)
        else:
            ticket = allocate()
            act(
                act(_status(TaskRun.objects.get(pk=ticket.task_id)), "claim"),
                "permanent_failure",
            )
            path = f"/admin/background/tasks/{ticket.task_id}/retry-family-preparation"
            owner, function = family_mail_tasks, "retry_preparation"
            receipts = TaskRun.objects.filter(retry_command_id=identifier)
        browser, _ = signed_in()
        yield SimpleNamespace(
            browser=browser,
            path=path,
            values=values,
            owner=owner,
            function=function,
            recorded=receipts.exists,
        )


def submit(form):
    """Run the actual POST endpoint under the exact restricted Web SQL identity."""
    with task_login(ServiceRole.WEB, exact=True):
        return form.browser.post(
            form.path,
            form.values,
            HTTP_X_CSRFTOKEN=form.browser.cookies["csrftoken"].value,
        )


def test_revoked_session_between_initial_check_and_effect_is_denied(form, monkeypatch):
    """A revocation winning before the command lock prevents every command kind."""
    original, first = delivery_views._principal, [True]

    def revoke(*args, **kwargs):
        """Commit the revocation after the original short authentication check."""
        principal = original(*args, **kwargs)
        if first[0]:
            first[0] = False
            PortalSession.objects.update(
                revoked_at=sessions.database_now(), version=F("version") + 1
            )
        return principal

    monkeypatch.setattr(delivery_views, "_principal", revoke)
    assert submit(form).status_code == 403
    assert not form.recorded()
    assert PortalSession.objects.get().revoked_at is not None


def test_expiry_after_domain_effect_rolls_back_all_commands(form, monkeypatch):
    """No command receipt may commit if its session expires while processing."""
    original = getattr(form.owner, form.function)
    deadline = PortalSession.objects.get().expires_at

    def expires(*args, **kwargs):
        """Advance only authentication's clock after the real domain effect."""
        result = original(*args, **kwargs)
        monkeypatch.setattr(sessions, "database_now", lambda: deadline)
        return result

    monkeypatch.setattr(form.owner, form.function, expires)
    assert submit(form).status_code == 403
    assert not form.recorded()


def test_private_post_evidence_is_marked_for_error_report_redaction(form):
    """Unexpected Django error reports must mask evidence, not only form errors."""
    response = submit(form)
    assert response.status_code == 302
    if "note" in form.values:
        assert response.wsgi_request.sensitive_post_parameters == ("note",)
