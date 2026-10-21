"""One message per recipient of a report send's frozen cohort (#1786).

The report pipeline's ``fanout`` phase (report-emails spec, "Report
pipeline", "Resolving recipients", "Testing redirect"): each cohort address
is rechecked and gets its own outbox message with the snapshot's email, or
a ``revoked`` recipient row when it is no longer a recipient. Each
recipient row and its message commit together, a page at a time; a
replayed page skips addresses it has already handled. Mail dispatch sends
nothing until the whole cohort is handled.
"""

from uuid import uuid4

from parishkit.stewardship.accounts.configuration_models import AppliedIntegration
from parishkit.stewardship.accounts.policy_schema import normalized_email
from parishkit.stewardship.campaigns.catchup_ownership import claim_event
from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.outbox_storage import create_message
from parishkit.stewardship.jobs.outbox_validation import (
    DeliveryIdentity,
    ReportRenderInput,
)
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.web.content import text_html

from .report_email_cohort import still_recipient, testing_label
from .report_email_models import ReportEmailRecipient, ReportEmailSnapshot
from .report_email_ownership import (
    bound_preparation,
    checkpoint_preparation,
    current_preparation,
)

LIMIT = 25
PURPOSE = "admin_report"


def testing_line(testing_recipient, label, address):
    """A Testing send's first line: who it went to, and who it was meant for."""
    return f"TEST — sent to {testing_recipient} instead of {label} {address}."


def recipient_render(snapshot, member, *, configuration_id, email, mode, testing):
    """The snapshot's email to one cohort address, routed as its mode requires.

    In Testing it goes to the Testing recipient instead, under "[TEST] " and
    a first line naming the intended recipient by how it was chosen (an
    Administrator, a Staff member or an outside address); Production
    applies no override.
    """
    address = member["address"]
    subject, html, text, routed = (
        snapshot.subject,
        snapshot.html,
        snapshot.text,
        address,
    )
    if mode == "testing":
        if normalized_email(testing) != testing:
            raise ValueError("Testing requires one canonical override recipient.")
        line = testing_line(testing, testing_label(member), address)
        subject = "[TEST] " + subject
        # text_html matches sanitizer output, so the delivery check passes.
        html = "<h2>TEST</h2><p>" + text_html(line) + "</p>" + html
        text = line + "\n\n" + text
        routed = testing
    return ReportRenderInput(
        configuration_id=configuration_id,
        template_id=None,
        sender=email.settings["sender"],
        reply_to=email.settings["reply_to"],
        intended_recipients=(address,),
        routed_recipients=(routed,),
        subject=subject,
        html=html,
        text=text,
    )


def fanout_report(claim):
    """Handle at most one page of cohort addresses under exact task ownership."""
    require_work_order()
    row = bound_preparation(_status(lock_task_claim(claim)))
    scope = current_preparation(row)
    if row.phase != "fanout":
        raise PermissionError("Report recipient allocation is not ready.")
    snapshot = ReportEmailSnapshot.objects.get(preparation_id=row.pk)
    handled = set(
        ReportEmailRecipient.objects.filter(snapshot=snapshot).values_list(
            "address", flat=True
        )
    )
    pending = [
        member for member in snapshot.recipients if member["address"] not in handled
    ]
    version = scope.runtime.active_configuration
    email = AppliedIntegration.objects.get(configuration=version, kind="email")
    correlation_id = claim_event(claim)
    for member in pending[:LIMIT]:
        identifier = uuid4()
        fields = dict(
            id=identifier,
            snapshot=snapshot,
            address=member["address"],
            kind=member["kind"],
            ref=member["ref"],
            actor_id=claim.worker_id,
            correlation_id=correlation_id,
        )
        if not still_recipient(row.definition_id, member):
            # Removed from the report, or no longer an Administrator or Staff:
            # an audited withdrawal, never a delivery (spec, the recheck).
            ReportEmailRecipient.objects.create(disposition="revoked", **fields)
            continue
        identity = DeliveryIdentity(
            scope_id=row.campaign_id,
            campaign_id=row.campaign_id,
            semantic_key=identifier,
            mode=row.mode,
            routing="testing_override" if row.mode == "testing" else "production",
            purpose=PURPOSE,
        )
        render = recipient_render(
            snapshot,
            member,
            configuration_id=version.pk,
            email=email,
            mode=row.mode,
            testing=scope.runtime.testing_recipient,
        )

        def admit(action, candidate, status, *, expected=identity):
            """Only this current page may allocate this exact semantic delivery."""
            lock_task_claim(claim)
            current_preparation(row)
            return action in {"create", "create_task"} and candidate == expected

        message = create_message(
            identity=identity,
            render=render,
            actor_id=claim.worker_id,
            correlation_id=correlation_id,
            command_id=identifier,
            admit=admit,
        )
        ReportEmailRecipient.objects.create(
            disposition="queued", outbox_id=message.message_id, **fields
        )
    return checkpoint_preparation(
        claim, phase="complete" if len(pending) <= LIMIT else "fanout"
    )
