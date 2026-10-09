"""Outgoing mail's bulk actions under the real web login and guards (#382 M4).

Each bulk action selects by the rules each message's own page uses,
previews an exact count, and resolves every message through the ordinary
per-message command, so a message that changed after the preview is
skipped, a repeated confirmation never repeats a resolution, and every
message keeps its own resolution record beside one bulk audit event.
"""

from contextlib import nullcontext
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs import delivery_bulk
from parishkit.stewardship.jobs.delivery_resolution_models import DeliveryResolution
from parishkit.stewardship.jobs.family_mail_content import open_family_credentials
from parishkit.stewardship.jobs.family_mail_dispatch import (
    begin_submission,
    finish_submission,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.outbox_storage import _status as delivery_status
from parishkit.stewardship.jobs.outbox_validation import (
    RenderInput,
    SealedSubstitutions,
)
from parishkit.stewardship.jobs.storage import _status

from .campaign_builders import campaign_clock, complete_empty_catchup
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_delivery_resolution_postgresql import failed_delivery, resolve
from .test_family_mail_dispatch_postgresql import claim, prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_test_postgresql import (  # noqa: F401
    deliver,
    family_test,
    prepare_tests,
    request_tickets,
)
from .test_policy_postgresql import user
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_views_postgresql import post
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)
NOTE = "Gmail's Sent folder shows none of these went out."


def uncertain_test(family_test):  # noqa: F811
    """One more chosen-Family test whose delivery is uncertain; returns it.

    The fixture has one deliverable Family, and its next test waits while an
    earlier one is uncertain, so each test message is made only after the
    previous one is settled.
    """
    harness, browser, path, _ = family_test
    request_tickets(browser, path, [1])
    message = prepare_tests(harness)[-1]
    deliver(harness, message, Status.UNKNOWN)
    act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
    message.refresh_from_db()
    assert message.state == "delivery_unknown"
    return message


def preview_unsent(browser, purpose="all"):
    """Preview recording every uncertain email as not sent."""
    return outgoing(
        browser,
        action="preview",
        kind="confirm_unsent",
        purpose=purpose,
        note=NOTE,
        checked="yes",
    )


def outgoing(browser, **values):
    """Post one bulk form to Outgoing mail as the signed-in Administrator."""
    with web_login():
        return post(browser, reverse("admin:deliveries"), values)


def bulk_events():
    """The bulk audit events' counts, oldest first."""
    found = []
    for event in AuditEvent.objects.filter(
        event_type="delivery_bulk_resolved"
    ).order_by("created_at"):
        context = AuditContext.objects.get(event=event).context
        found.append((context["count"], context["matching_count"]))
    return found


def test_confirm_all_not_sent_previews_then_skips_what_changed(family_test):  # noqa: F811
    """One preview, a required note and check, a refusal, then a resolution.

    An email settled on its own page after the preview is skipped rather
    than forced; a later preview records the next uncertain email as not
    sent, and confirming it again repeats nothing.
    """
    harness, browser, _, _ = family_test
    first = uncertain_test(family_test)
    with web_login():
        page = browser.get(reverse("admin:deliveries"))
    assert page.status_code == 200
    assert page.context["bulk_overview"] == {
        "confirm_unsent": {"total": 1, "purposes": [("family_test", 1)]}
    }
    assert b"Fix many emails at once" in page.content
    # The note and the records check are both required, and the choice
    # must be one the action takes.
    for values in (
        dict(note="  "),
        dict(checked=None),
        dict(kind="retry_failed"),
        dict(purpose="unknown"),
    ):
        fields = (
            dict(
                action="preview",
                kind="confirm_unsent",
                purpose="all",
                note=NOTE,
                checked="yes",
            )
            | values
        )
        refused = outgoing(
            browser, **{key: value for key, value in fields.items() if value}
        )
        assert refused.status_code == 400, values
        assert refused.context["bulk_errors"]
        assert b"data-error-summary" in refused.content
    review = preview_unsent(browser, "family_test")
    assert review.status_code == 200
    preview = review.context["bulk_review"]
    assert preview["count"] == 1 and preview["form"] == "bulk-confirm_unsent"
    assert b"Record 1 email as not sent" in review.content
    # Previewing changed nothing.
    assert not DeliveryResolution.objects.exists()
    resolve(harness, user("admin@example.org"), first, "accept")
    skipped = outgoing(browser, action="confirm", preview=preview["token"], note=NOTE)
    assert skipped.status_code == 200
    result = skipped.context["bulk_result"]
    assert (result.total, result.resolved, result.newly, result.skipped) == (1, 0, 0, 1)
    first.refresh_from_db()
    assert first.state == "delivered"
    assert not DeliveryResolution.objects.filter(action="confirm_unsent").exists()

    second = uncertain_test(family_test)
    preview = preview_unsent(browser).context["bulk_review"]
    assert preview["count"] == 1
    done = outgoing(browser, action="confirm", preview=preview["token"], note=NOTE)
    result = done.context["bulk_result"]
    assert (result.resolved, result.newly, result.skipped, result.remaining) == (
        1,
        1,
        0,
        0,
    )
    second.refresh_from_db()
    assert second.state == "permanent_failure"
    assert second.reason == "admin_confirmed_unsent"
    assert second.evidence_note == NOTE
    assert TaskRun.objects.filter(root_id=second.task_id).count() == 1
    resolution = DeliveryResolution.objects.get(message_id=second.pk)
    assert resolution.action == "confirm_unsent" and resolution.retry_task_id is None
    assert AuditEvent.objects.filter(
        subject_id=resolution.pk, event_type="delivery_resolution_confirm_unsent"
    ).exists()
    # Confirming the same preview again replays: nothing new is resolved.
    again = outgoing(browser, action="confirm", preview=preview["token"], note=NOTE)
    result = again.context["bulk_result"]
    assert (result.resolved, result.newly, result.skipped) == (1, 0, 0)
    assert DeliveryResolution.objects.filter(action="confirm_unsent").count() == 1
    assert bulk_events() == [(0, 1), (1, 1)]
    # Nothing qualifies any more.
    empty = preview_unsent(browser)
    assert empty.status_code == 409 and empty.context["bulk_errors"]


def test_a_preview_belongs_to_its_administrator_and_expires(family_test, monkeypatch):  # noqa: F811
    """Another Administrator's or an expired preview changes nothing."""
    _, browser, _, _ = family_test
    uncertain_test(family_test)
    review = preview_unsent(browser)
    token = review.context["bulk_review"]["token"]
    with pytest.raises(PermissionError):
        delivery_bulk.load_preview(token, uuid4(), NOTE)
    monkeypatch.setattr(delivery_bulk, "PREVIEW_SECONDS", -1)
    expired = outgoing(browser, action="confirm", preview=token, note=NOTE)
    assert expired.status_code == 409 and expired.context["bulk_errors"]
    altered = outgoing(browser, action="confirm", preview=token[:-2] + "xx", note=NOTE)
    assert altered.status_code == 409
    monkeypatch.setattr(delivery_bulk, "PREVIEW_SECONDS", 900)
    # The preview carries only the note's digest: another note is refused.
    other = outgoing(browser, action="confirm", preview=token, note="Other note")
    assert other.status_code == 409
    missing = outgoing(browser, action="confirm", preview=token)
    assert missing.status_code == 400
    assert not DeliveryResolution.objects.exists()
    assert not bulk_events()


def test_retry_all_failed_runs_the_ordinary_retry_in_batches(family_mail):  # noqa: F811
    """A failed invitation is retried as its own page retries it, a batch at a time."""
    from parishkit.stewardship.jobs.delivery_views import _retry_inputs

    principal = user("admin@example.org")
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail, Status.PERMANENT)
        render_id = message.render_id
        with web_login():
            assert [
                item["id"] for item in delivery_bulk.candidates("retry_failed")
            ] == [message.pk]
            assert delivery_bulk.candidates("confirm_unsent") == []
            review = delivery_bulk.preview(
                principal.pk,
                kind="retry_failed",
                purpose="initial",
                note=NOTE,
                checked=False,
            )
            binding = delivery_bulk.load_preview(review["token"], principal.pk, NOTE)

            def run(limit):
                """Apply the preview as the page does, with ``limit`` per batch."""
                return delivery_bulk.apply_preview(
                    family_mail.service.store,
                    principal.pk,
                    binding,
                    scope=nullcontext,
                    admit=lambda: None,
                    preparation_inputs=lambda purpose: dict(
                        general=family_mail.rings.general,
                        public=family_mail.rings.public,
                        public_origin="http://localhost:8000",
                    ),
                    limit=limit,
                )

            waiting = run(0)
            assert (waiting.resolved, waiting.remaining) == (0, 1)
            done = run(delivery_bulk.BATCH)
            assert (done.resolved, done.newly, done.skipped) == (1, 1, 0)
        message.refresh_from_db()
        assert message.state == "pending" and message.render_id != render_id
        resolution = DeliveryResolution.objects.get(message_id=message.pk)
        assert resolution.action == "retry_failed" and resolution.evidence_note == NOTE
        retried = TaskRun.objects.get(pk=resolution.retry_task_id)
        assert retried.parent_id == message.task_id and retried.state == "queued"
        # The page's own retry inputs are what the view passes.
        assert callable(_retry_inputs)
    assert bulk_events() == [(1, 1)]
    assert OutboxMessage.objects.count() == 1


def credentials(harness, message):
    """The Family code and link token a prepared message would send.

    Opened from the message's sealed substitutions with the private keyring,
    as the mail sender opens them, plus the token generation and credential
    epoch it was sealed under.
    """
    message.refresh_from_db()
    render = message.render
    reference = open_family_credentials(
        identity=delivery_status(message).identity,
        render=RenderInput(
            **{
                field: getattr(render, field)
                for field in (
                    "configuration_id",
                    "template_id",
                    "sender",
                    "reply_to",
                    "intended_recipients",
                    "routed_recipients",
                    "subject",
                    "html",
                    "text",
                )
            }
        ),
        sealed=SealedSubstitutions(
            message.sealed_substitutions,
            message.token_generation_id,
            message.credential_epoch_id,
        ),
        private=harness.rings.private,
    )
    return (
        reference.code,
        reference.token_id,
        message.token_generation_id,
        message.credential_epoch_id,
    )


def test_a_bulk_retried_invitation_keeps_its_code_and_link(family_mail):  # noqa: F811
    """Codes and links Families already received stay valid (Production).

    The bulk retry re-renders and reseals the invitation, but with the same
    Family code and the same link token, generation and credential epoch,
    so nothing already emailed is invalidated.
    """
    from parishkit.stewardship.campaigns.credential_models import FamilyAccessToken

    principal = user("admin@example.org")
    family_mail = activate_response_service(family_mail)
    complete_empty_catchup(family_mail.campaign, uuid4())
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        # failed_delivery's steps, reading the credentials the invitation
        # was first prepared with before its failure clears them.
        message = prepare(family_mail)
        assert message.mode == "production"
        before = credentials(family_mail, message)
        assert before[2] is not None and before[3] is not None
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            begin_submission(
                message.pk,
                execution.claim,
                private=family_mail.rings.private,
                public_origin="http://localhost:8000",
            )
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(Status.PERMANENT, 1)
            )
        act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
        message.refresh_from_db()
        assert message.state == "permanent_failure"
        tokens = set(
            FamilyAccessToken.objects.filter(destroyed_at__isnull=True).values_list(
                "id", flat=True
            )
        )
        assert before[1] in tokens
        with web_login():
            review = delivery_bulk.preview(
                principal.pk,
                kind="retry_failed",
                purpose="all",
                note=NOTE,
                checked=False,
            )
            assert review["count"] == 1
            result = delivery_bulk.apply_preview(
                family_mail.service.store,
                principal.pk,
                delivery_bulk.load_preview(review["token"], principal.pk, NOTE),
                scope=nullcontext,
                admit=lambda: None,
                preparation_inputs=lambda purpose: dict(
                    general=family_mail.rings.general,
                    public=family_mail.rings.public,
                    public_origin="http://localhost:8000",
                ),
            )
        assert (result.newly, result.skipped) == (1, 0)
        message.refresh_from_db()
        assert message.state == "pending"
        assert credentials(family_mail, message) == before
        assert (
            set(
                FamilyAccessToken.objects.filter(destroyed_at__isnull=True).values_list(
                    "id", flat=True
                )
            )
            == tokens
        )
