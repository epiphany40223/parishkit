"""Real-session Admin metadata, warning privacy, and verified-clearance forms."""

from uuid import uuid4

import pytest
from django.db import connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.audit.models import AuditEvent, OperationalLog
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    FamilyDeliveryStatus,
)
from parishkit.stewardship.jobs.delivery_states import DeliveryAction
from parishkit.stewardship.jobs.family_mail_dispatch import (
    begin_submission,
    finish_submission,
)
from parishkit.stewardship.jobs.outbox_models import OutboxEvent
from parishkit.stewardship.jobs.outbox_storage import change_message
from parishkit.stewardship.jobs.recipient_models import RecipientRefusalResolution
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import campaign_clock, change
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import claim, prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_recipient_suppressions_postgresql import refused, remember

pytestmark = pytest.mark.django_db(transaction=True)


def uncertain(harness):
    """Create actual unknown provider state, not an unconstrained ORM fixture."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            begin_submission(
                message.pk,
                execution.claim,
                private=harness.rings.private,
                public_origin="http://localhost:8000",
            )
            finish_submission(
                message.pk,
                execution.claim,
                FamilyDeliveryResult(FamilyDeliveryStatus.UNKNOWN, 1),
            )
    message.refresh_from_db()
    return message


def test_uncertainty_warns_once_and_admin_metadata_never_discloses_payload(
    family_mail,  # noqa: F811
    google,
):
    """The persistent warning survives finished/failed Task status independently."""
    message = uncertain(family_mail)
    warning = OperationalLog.objects.get(event="delivery_unknown")
    assert warning.level == "WARNING"
    assert warning.context == {"message_id": str(message.pk)}
    # The low-level journal's exact command replay also cannot duplicate warnings.
    event = OutboxEvent.objects.get(message=message, action="mark_unknown")
    from parishkit.stewardship.jobs.outbox_validation import DeliveryEvidence

    change_message(
        message_id=message.pk,
        action=DeliveryAction.MARK_UNKNOWN,
        command_id=event.command_id,
        expected_version=event.version - 1,
        actor_id=event.actor_id,
        correlation_id=event.correlation_id,
        evidence=DeliveryEvidence(
            **{
                key: getattr(event, key)
                for key in DeliveryEvidence.__dataclass_fields__
            }
        ),
        admit=lambda *args: True,
    )
    assert OperationalLog.objects.filter(event="delivery_unknown").count() == 1
    browser, _ = signed_in()
    activity = PortalSession.objects.get().last_activity_at
    with task_login(ServiceRole.WEB, exact=True):
        for path in ("/admin/", "/admin/deliveries", f"/admin/deliveries/{message.pk}"):
            response = browser.get(path)
            assert response.status_code == 200
            assert b"Delivery unknown" in response.content
            assert family_mail.code.encode() not in response.content
            assert message.sealed_substitutions.encode() not in response.content
            assert b"sealed_substitutions" not in response.content
        result = browser.get("/admin/background/counts")
        assert result.json()["delivery_unknown"] == 1
    # Dashboard is activity; the status pages themselves must remain passive.
    assert activity < PortalSession.objects.get().last_activity_at
    before = PortalSession.objects.get().last_activity_at
    for path in ("/admin/deliveries", f"/admin/deliveries/{message.pk}"):
        assert browser.get(path).status_code == 200
        assert PortalSession.objects.get().last_activity_at == before


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_delivery_pages_and_warning_are_admin_only(family_mail, google, role):  # noqa: F811
    """Neither direct UUID navigation nor raw page chrome grants Ministry access."""
    message = uncertain(family_mail)
    store = family_mail.service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=(role,)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    for path in (
        "/admin/deliveries",
        f"/admin/deliveries/{message.pk}",
        "/admin/deliveries/refusals",
    ):
        assert browser.get(path).status_code == 403
    assert b"data-delivery-warning" not in browser.get("/admin/").content


@pytest.mark.parametrize(
    "query",
    [
        "state=arbitrary",
        "q=invalid",
        "page=0",
        "size=501",
        "state=all&state=pending",
        "token=private",
    ],
)
def test_delivery_filters_are_closed(auth_service, google, query):
    """Unknown/duplicate fields and unbounded pagination fail before reads."""
    browser, _ = signed_in()
    assert browser.get("/admin/deliveries?" + query).status_code == 400


def test_authority_is_rechecked_after_render(family_mail, google, monkeypatch):  # noqa: F811
    """An in-flight revocation cannot reveal even operational Family identities."""
    from parishkit.stewardship.jobs import delivery_views

    message = uncertain(family_mail)
    browser, _ = signed_in()
    original = delivery_views.render

    def revoked(*args, **kwargs):
        """Complete rendering then revoke the actual current principal."""
        result = original(*args, **kwargs)
        PortalUser.objects.update(disabled=True, version=F("version") + 1)
        return result

    monkeypatch.setattr(delivery_views, "render", revoked)
    response = browser.get(f"/admin/deliveries/{message.pk}")
    assert response.status_code == 403
    assert str(message.pk).encode() not in response.content
    assert not AuditEvent.objects.filter(event_type="delivery_viewed").exists()


def test_web_cannot_read_render_or_sealed_substitution(family_mail):  # noqa: F811
    """Metadata grants do not quietly become general outbox read authority."""
    uncertain(family_mail)
    from django.db import DatabaseError

    with task_login(ServiceRole.WEB, exact=True):
        for sql in (
            "SELECT sealed_substitutions FROM stewardship_outbox_message",
            "SELECT html FROM stewardship_outbox_render",
            "SELECT evidence_note FROM stewardship_outbox_event",
        ):
            with (
                pytest.raises(DatabaseError) as error,
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(sql)
            assert error.value.__cause__.sqlstate == "42501"


def test_verified_clearance_form_is_audited_and_replay_safe(response_service, google):
    """The form carries current source identity and never places notes in URLs."""
    harness = activate_response_service(response_service)
    refusal = remember(refused(harness))
    browser, _ = signed_in()
    path = f"/admin/deliveries/refusals/{refusal.pk}"
    current = SourceCurrent.objects.get()
    values = dict(
        command_id=str(uuid4()),
        source_snapshot_id=str(current.snapshot_id),
        source_generation=str(current.generation),
        verified="yes",
        note="Verified with the Family <script>not executable</script>",
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        activity = PortalSession.objects.get().last_activity_at
        page = browser.get(path)
        assert page.status_code == 200 and b"csrfmiddlewaretoken" in page.content
        assert PortalSession.objects.get().last_activity_at == activity
        assert browser.post(path + "/clear", values).status_code == 403
        for _ in range(2):
            result = browser.post(
                path + "/clear",
                values,
                HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
            )
            assert result.status_code == 302 and result["Location"] == path
        result = browser.get(path)
        assert b"&lt;script&gt;not executable&lt;/script&gt;" in result.content
        assert b"<script>not executable</script>" not in result.content
    assert RecipientRefusalResolution.objects.count() == 1


@pytest.mark.parametrize(
    "action",
    ["note", "accept", "confirm_unsent", "resend", "retry_failed", "retry_unsent"],
)
def test_delivery_forms_apply_once_with_current_session_and_csrf(
    family_mail,  # noqa: F811
    google,
    monkeypatch,
    settings,
    action,  # noqa: F811
):
    """Actual evidence forms bind exact state and never perform provider work."""
    from parishkit.stewardship.accounts import family_authentication
    from parishkit.stewardship.campaigns.schedule_models import (
        ScheduleFulfillment,
        ScheduleOccurrence,
    )
    from parishkit.stewardship.jobs.delivery_resolution_models import DeliveryResolution
    from parishkit.stewardship.jobs.models import TaskRun

    from .test_delivery_resolution_postgresql import failed_delivery

    settings.STEWARDSHIP_PUBLIC_ORIGIN = "http://localhost:8000"
    monkeypatch.setattr(family_authentication, "runtime", lambda: family_mail.rings)
    status = {
        "retry_failed": FamilyDeliveryStatus.PERMANENT,
        "retry_unsent": None,
    }.get(action, FamilyDeliveryStatus.UNKNOWN)
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail, status)
        old_version, old_attempt = message.version, message.attempt
        browser, _ = signed_in()
        path = f"/admin/deliveries/{message.pk}"
        values = dict(
            command_id=str(uuid4()),
            expected_version=str(message.version),
            action=action,
            note="Independent confirmation <script>evidence</script>",
        )
        if action == "resend":
            values["duplicate_acknowledged"] = "yes"
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            assert browser.get(path).status_code == 200
            assert browser.post(path + "/resolve", values).status_code == 403
            for _ in range(2):
                response = browser.post(
                    path + "/resolve",
                    values,
                    HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
                )
                assert response.status_code == 302 and response["Location"] == path
            result = browser.get(path)
            assert b"&lt;script&gt;evidence&lt;/script&gt;" in result.content
            assert b"<script>evidence</script>" not in result.content
            if action == "accept":
                assert b"Retry is currently unavailable" not in result.content
            if action == "confirm_unsent":
                # The Admin record is not presented as a provider refusal.
                assert b"Not sent, per provider records; no resend" in result.content
                assert b"Not accepted; delivery failed" not in result.content
        assert DeliveryResolution.objects.count() == 1
        message.refresh_from_db()
        command = DeliveryResolution.objects.get()
        assert command.preparation is None
        assert message.attempt == old_attempt
        retrying = action in {"resend", "retry_failed", "retry_unsent"}
        assert message.state == (
            "pending"
            if retrying
            else "delivered"
            if action == "accept"
            else "permanent_failure"
            if action == "confirm_unsent"
            else "delivery_unknown"
        )
        assert message.version == old_version + (
            0 if action == "note" else 2 if action == "resend" else 1
        )
        assert TaskRun.objects.get(pk=message.task_id).state == "failed"
        assert TaskRun.objects.filter(root_id=message.task_id).count() == (
            2 if retrying else 1
        )
        if retrying:
            child = TaskRun.objects.get(pk=command.retry_task_id)
            assert child.state == "queued" and child.parent_id == message.task_id
        assert ScheduleFulfillment.objects.count() == (1 if action == "accept" else 0)
        if action in {"accept", "confirm_unsent"}:
            assert ScheduleOccurrence.objects.get(pk=message.semantic_key).state == (
                "succeeded" if action == "accept" else "failed"
            )
        assert AuditEvent.objects.filter(subject_id=command.pk).count() == 1
        if action != "note":
            stale = values | {"command_id": str(uuid4())}
            assert (
                browser.post(
                    path + "/resolve",
                    stale,
                    HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
                ).status_code
                == 409
            )
            assert DeliveryResolution.objects.count() == 1


def test_invalid_form_has_private_accessible_recovery(family_mail, google):  # noqa: F811
    """Validation errors are navigable HTML, not JSON or an echo of private data."""
    message = uncertain(family_mail)
    browser, _ = signed_in()
    path = f"/admin/deliveries/{message.pk}"
    with task_login(ServiceRole.WEB, exact=True):
        assert browser.get(path).status_code == 200
        response = browser.post(
            path + "/resolve",
            dict(
                command_id="invalid",
                expected_version="1",
                action="accept",
                note="private-evidence-marker",
            ),
            HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
        )
    assert response.status_code == 400
    assert response["Content-Type"].startswith("text/html")
    assert b'role="alert"' in response.content
    assert b'"/admin/deliveries"' in response.content
    assert b"private-evidence-marker" not in response.content
    assert response["Cache-Control"] == "no-store"


def test_preparation_retry_form_preserves_failed_task_and_hides_stale_action(
    family_mail,  # noqa: F811
    google,
):
    """Explicit local preparation retry creates one child, not a second ticket."""
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_family_mail_preparation_postgresql import allocate
    from .test_taskrun_postgresql import act

    browser, _ = signed_in()
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        ticket = allocate()
        act(
            act(_status(TaskRun.objects.get(pk=ticket.task_id)), "claim"),
            "permanent_failure",
        )
        path = f"/admin/background/tasks/{ticket.task_id}"
        page_path = f"/admin/background/task/{ticket.task_id}"
        values = {"command_id": str(uuid4())}
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = browser.get(page_path)
            assert (
                page.status_code == 200 and b"retry-family-preparation" in page.content
            )
            assert (
                browser.post(path + "/retry-family-preparation", values).status_code
                == 403
            )
            for _ in range(2):
                result = browser.post(
                    path + "/retry-family-preparation",
                    values,
                    HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
                )
                assert result.status_code == 302
            assert b"retry-family-preparation" not in browser.get(page_path).content
            conflict = browser.post(
                path + "/retry-family-preparation",
                {"command_id": str(uuid4())},
                HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
            )
            assert conflict.status_code == 409
        assert TaskRun.objects.get(pk=ticket.task_id).state == "failed"
        assert TaskRun.objects.filter(root_id=ticket.task_id).count() == 2
