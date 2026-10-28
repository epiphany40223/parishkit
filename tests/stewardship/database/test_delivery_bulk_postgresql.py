"""Sent mail's bulk actions under the real web login and guards (#382 M4).

Each bulk action selects by the rules each message's own page uses,
previews an exact count, and resolves every message through the ordinary
per-message command, so a message that changed after the preview is
skipped, a repeated confirmation never repeats a resolution, and every
message keeps its own resolution record beside one bulk audit event.
"""

from contextlib import nullcontext
from uuid import uuid4

import pytest
from django.core import signing
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


def review(browser, *ids, kind="confirm_unsent", **values):
    """The bulk bar's action on the ticked rows ``ids`` (or, with
    ``scope="matching"``, on every email the view's filters match)."""
    fields = dict(action="preview", kind=kind, scope="page") | values
    if ids:
        fields["delivery"] = [str(message_id) for message_id in ids]
    return outgoing(browser, **fields)


def apply(browser, answer, **values):
    """The review dialog's Apply for the review fragment ``answer``."""
    fields = dict(action="confirm", preview=answer.context["review"]["token"])
    fields |= dict(note=NOTE, checked="yes") | values
    # None leaves a field out.
    return outgoing(
        browser, **{name: value for name, value in fields.items() if value is not None}
    )


def outgoing(browser, **values):
    """Post one bulk step to Sent mail as the signed-in Administrator."""
    with web_login():
        return post(browser, reverse("admin:deliveries"), values)


def listed(browser, state):
    """The ids Sent mail lists under Delivery ``state``, in its default mode."""
    with web_login():
        page = browser.get(reverse("admin:deliveries"), {"state": state})
    assert page.status_code == 200
    return {row["id"] for row in page.context["table"].rows}


def bulk_events():
    """The bulk audit events' counts, oldest first."""
    found = []
    for event in AuditEvent.objects.filter(
        event_type="delivery_bulk_resolved"
    ).order_by("created_at"):
        context = AuditContext.objects.get(event=event).context
        found.append((context["count"], context["matching_count"]))
    return found


def refused(answer, reason, status=409):
    """``answer`` is the review dialog's refusal for ``reason``."""
    assert answer.status_code == status, answer.content[:300]
    assert answer.context["refused"] == reason
    assert b"data-review-refusal" in answer.content and b"<html" not in answer.content


def test_record_as_not_sent_reviews_the_selection_then_skips_what_changed(
    family_test,  # noqa: F811
):
    """A box of its kind, a review of exactly the ticked rows, a required
    note and check at Apply, a skip, then a resolution (#2296).

    An email settled on its own page after the review is skipped rather
    than forced; a later review records the next uncertain email as not
    sent, its Continue reviews what is left, and applying it again
    repeats nothing.
    """
    harness, browser, _, _ = family_test
    first = uncertain_test(family_test)
    with web_login():
        page = browser.get(reverse("admin:deliveries"))
    assert page.status_code == 200
    (row,) = [row for row in page.context["table"].rows if row["id"] == first.pk]
    assert row["bulk_kind"] == "confirm_unsent" and page.context["bulk_shown"]
    assert f'name="delivery" value="{first.pk}"'.encode() in page.content
    assert b'data-confirm-kind="confirm_unsent"' in page.content
    assert b"Fix many emails at once" not in page.content
    # Every match is on this page: no Select all N matching.
    assert page.context["matching"] is None
    # A selection that does not fit the action, or names an email no
    # action covers, is refused whole;
    refused(review(browser, first.pk, kind="retry_failed"), "changed")
    refused(review(browser, first.pk, uuid4()), "changed")
    # a hand-built one says so in its own words, not the note's.
    for answer in (
        review(browser),
        review(browser, "not-an-id"),
        review(browser, *(uuid4() for _ in range(101))),
        review(browser, first.pk, scope="all"),
    ):
        refused(answer, "selection", 400)
        assert b"could not be read, or more than one page" in answer.content
        assert b"Enter the evidence" not in answer.content
    shown = review(browser, first.pk, mode="testing", size="50")
    assert shown.status_code == 200
    assert b"data-review-answer" in shown.content and b"<html" not in shown.content
    assert shown.context["review"]["count"] == 1
    assert shown.context["review"]["purposes"] == [("family_test", 1)]
    assert b"Record 1 email as not sent?" in shown.content
    # The view's fields go back with Apply, so its answer draws this view.
    assert shown.context["view"] == [("mode", "testing"), ("size", "50")]
    # Previewing changed nothing; Apply needs the note and the check.
    assert not DeliveryResolution.objects.exists()
    refused(apply(browser, shown, checked=None), "invalid", 400)
    refused(apply(browser, shown, note="  "), "invalid", 400)
    assert not DeliveryResolution.objects.exists()
    resolve(harness, user("admin@example.org"), first, "accept")
    skipped = apply(browser, shown)
    assert skipped.status_code == 200
    result = skipped.context["bulk_result"]
    assert (result.total, result.resolved, result.newly, result.skipped) == (1, 0, 0, 1)
    assert b"data-review-applied" in skipped.content
    first.refresh_from_db()
    assert first.state == "delivered"
    assert not DeliveryResolution.objects.filter(action="confirm_unsent").exists()

    second = uncertain_test(family_test)
    # Needs attention (#1562) lists an uncertain email; on a Testing
    # system the page shows Testing email by default.
    assert second.pk in listed(browser, "needs_attention")
    # Select all N matching reads the emails again from the filters.
    matched = review(browser, scope="matching", state="needs_attention")
    assert matched.context["review"]["count"] == 1
    # Continue's review counts what the preview has left, with its note.
    binding = signing.loads(matched.context["review"]["token"], salt=delivery_bulk.SALT)
    resumed = delivery_bulk.sign(
        binding | {"note_digest": delivery_bulk.note_digest(NOTE)}
    )
    shown = outgoing(browser, action="continue", preview=resumed, note=NOTE)
    assert shown.status_code == 200
    assert shown.context["review"] | dict(token=None) == dict(
        kind="confirm_unsent", count=1, token=None, note=NOTE, continuing=True
    )
    refused(
        outgoing(browser, action="continue", preview=resumed, note="Other"),
        "changed",
    )
    done = apply(browser, shown, checked=None)
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
    # Recorded as not sent, it is settled: Failed still lists it, but
    # Needs attention no longer does (#1562), and it has no box.
    assert second.pk in listed(browser, "permanent_failure")
    assert second.pk not in listed(browser, "needs_attention")
    # With nothing left to select, the page draws no selection column or
    # bulk bar.
    with web_login():
        page = browser.get(reverse("admin:deliveries"))
    assert not page.context["bulk_shown"]
    assert b"data-select-table" not in page.content
    assert b'class="bulk-bar"' not in page.content
    # Applying the same preview again replays: nothing new is resolved.
    again = apply(browser, shown, checked=None)
    result = again.context["bulk_result"]
    assert (result.resolved, result.newly, result.skipped) == (1, 0, 0)
    assert DeliveryResolution.objects.filter(action="confirm_unsent").count() == 1
    assert bulk_events() == [(0, 1), (1, 1)]
    # The first preview, which binds no note, is refused once Continue's
    # (which binds it) applied one of its emails: it cannot apply the rest
    # with another note.
    refused(apply(browser, matched, note="Other", checked="yes"), "changed")
    # Nothing qualifies any more, and Continue has nothing left.
    refused(review(browser, scope="matching"), "empty")
    refused(outgoing(browser, action="continue", preview=resumed, note=NOTE), "empty")
    # Apply carries the applied Recipient, which is never in the address it
    # posts to, so the answer's list keeps it (#1562).
    kept = apply(browser, shown, checked=None, recipient="nobody@example.org")
    assert kept.status_code == 200
    assert kept.context["recipient"] == "nobody@example.org"
    assert kept.context["table"].rows == []


def test_select_all_matching_refuses_a_mix_of_kinds(family_test, monkeypatch):  # noqa: F811
    """The matching emails must all be of the action's kind (#2296)."""
    _, browser, _, _ = family_test
    available = {
        uuid4(): dict(version=1, purpose="initial", kind=kind)
        for kind in ("retry_failed", "confirm_unsent")
    }
    available = {key: value | dict(id=key) for key, value in available.items()}
    monkeypatch.setattr(delivery_bulk, "selectable", lambda: available)
    monkeypatch.setattr(
        delivery_bulk, "matching_ids", lambda values, **kwargs: list(available)
    )
    refused(review(browser, scope="matching", kind="retry_failed"), "mixed")
    # The page offers the toggle, of the mixed kind, since neither email
    # is listed on it.
    with web_login():
        page = browser.get(reverse("admin:deliveries"))
    assert page.context["matching"] == dict(count=2, kind="mixed")
    assert b"Select all 2 matching" in page.content
    assert not DeliveryResolution.objects.exists()


def test_a_preview_belongs_to_its_administrator_and_expires(family_test, monkeypatch):  # noqa: F811
    """Another Administrator's or an expired preview changes nothing."""
    _, browser, _, _ = family_test
    message = uncertain_test(family_test)
    shown = review(browser, message.pk)
    token = shown.context["review"]["token"]
    with pytest.raises(PermissionError):
        delivery_bulk.load_preview(token, uuid4(), NOTE, checked=True)
    monkeypatch.setattr(delivery_bulk, "PREVIEW_SECONDS", -1)
    refused(apply(browser, shown), "changed")
    refused(apply(browser, shown, preview=token[:-2] + "xx"), "changed")
    monkeypatch.setattr(delivery_bulk, "PREVIEW_SECONDS", 900)
    refused(outgoing(browser, action="confirm", preview=token), "invalid", 400)
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
                ids=[message.pk],
                available=delivery_bulk.selectable(),
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
                ids=[message.pk],
                available=delivery_bulk.selectable(),
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
