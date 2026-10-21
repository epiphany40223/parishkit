"""The Ministry leader email owner (#1592): one Administrator's request at a time.

An Administrator's Email Ministry leaders Apply records a
``LeaderEmailRequest`` whose database guard fills in each leader's
Ministries and open request counts. This owner delivers it through the same
engine as the Administrator notices (``operational_fanout``,
``operational_dispatch``, ``operational_mail_tasks``; background-processing
specification, "Ministry leader emails"), with three differences:

- its messages are routed like campaign mail, to the Testing recipient the
  request recorded in Testing (``testing_routed``), though they belong to no
  campaign;
- each recipient row keeps the compiled subject and bodies, and every
  attempt sends exactly them (``keeps_content``): the content is compiled
  once, from the request's recorded facts, by the preparation Task;
- before each submission, the address must still be a user whose role is
  Ministry leader, the deployment mode the request's and, in Testing, the
  Testing recipient the request's, or the unsent email is cancelled; a
  restore under review holds it, and so does Pause and resume mail while
  the current campaign's delivery is paused (a Production email only),
  and a held email is not claimed until Resume (``held``);
- a request whose mode or Testing recipient changed before it was prepared
  is cancelled, not sent (``superseded``), and Last emailed says so.

The private mail helper is the weekly report's (``submit_weekly``): a
closed, one-recipient report mail with no attachment, whose markup
validator admits this compiled content unchanged.
"""

from functools import partial
from uuid import UUID

from django.db.models import Exists, OuterRef

from parishkit.stewardship.accounts.configuration_models import (
    AppliedIntegration,
    Parish,
)
from parishkit.stewardship.accounts.policy_models import AddressRule
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.work_locks import require_work_order

from .alert_owner import AlertOwner
from .family_mail_dispatch import FamilyDeliveryHeld
from .leader_email_content import (
    LeaderEmailFacts,
    recorded_ministries,
    render_leader_email,
)
from .leader_email_models import (
    LeaderEmailCohort,
    LeaderEmailRecipient,
    LeaderEmailRequest,
)
from .outbox_validation import RenderInput

TASK_TYPE = "leader_email_prepare"
NAMESPACE = UUID("6c1f4d2e-9b7a-4e35-8a61-2f0d9c4b7e13")
# The one role whose users these emails go to (Administrator decision on
# #1592: no Staff or Administrator copy).
ROLE = "ministry_leader"


def _configured():
    """A parish and an email channel are all a request needs to be prepared."""
    version = SystemConfiguration.objects.values_list(
        "active_configuration_id", flat=True
    ).first()
    return (
        version is not None
        and Parish.objects.filter(configuration_id=version).exists()
        and AppliedIntegration.objects.filter(
            configuration_id=version, kind="email"
        ).exists()
    )


def _pending(limit):
    """Requests no preparation Task owns yet, oldest first.

    ``NOT EXISTS`` for the same reason as ``operational_models
    .unowned_notices``: an anti-join on the Task type index.
    """
    from .models import TaskRun

    owned = TaskRun.objects.filter(
        task_type=TASK_TYPE, domain_request_id=OuterRef("pk")
    )
    return tuple(
        LeaderEmailRequest.objects.filter(~Exists(owned))
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)[:limit]
    )


def _source_exists(request_id):
    """Only a recorded request may be prepared."""
    return LeaderEmailRequest.objects.filter(pk=request_id).exists()


def _superseded(request_id):
    """Whether the deployment's mode or Testing recipient changed since Apply.

    Such a request is never prepared under the other one: the preparation
    Task is cancelled instead (``operational_fanout``), and the Users page's
    Last emailed shows why (``accounts.leader_emails.leader_rows``).
    """
    request = LeaderEmailRequest.objects.values("mode", "testing_recipient").get(
        pk=request_id
    )
    runtime = SystemConfiguration.objects.values("mode", "testing_recipient").get()
    testing = runtime["testing_recipient"] if runtime["mode"] == "testing" else None
    return (request["mode"], request["testing_recipient"]) != (runtime["mode"], testing)


def _held(message):
    """Whether Pause and resume mail holds this unsent email now.

    Only a Production email is held, as for campaign mail (admin-portal
    specification, "Live delivery pause"); admission then takes no claim.
    """
    return (
        message.mode == "production"
        and SystemConfiguration.objects.filter(
            current_campaign__delivery_paused=True
        ).exists()
    )


def _route(store):
    """The current routing: the operational reader's, without its Admin list."""
    from . import operational_fanout

    return operational_fanout.current_routing(store)


def _capture(row, claim, store):
    """Freeze the request's addresses under the current configuration.

    A request whose deployment mode (or, in Testing, Testing recipient)
    changed before it was prepared is never sent under the other one: the
    engine cancels its Task first (``_superseded``); the refusals here only
    back that up, so no email is created either way.
    """
    route = _route(store)
    if route.sender is None or route.reply_to is None:
        raise FamilyDeliveryHeld("Ministry leader mail channel is not configured.")
    request = LeaderEmailRequest.objects.get(pk=row.domain_request_id)
    if route.mode.value != request.mode:
        raise PermissionError("The deployment mode changed since the request.")
    if request.testing_recipient != _testing_recipient():
        raise PermissionError("The Testing recipient changed since the request.")
    return LeaderEmailCohort.objects.create(
        request=request,
        configuration_id=route.configuration_id,
        parish=Parish.objects.get(configuration_id=route.configuration_id),
        mode=request.mode,
        testing_recipient=request.testing_recipient,
        addresses=list(request.addresses),
        recipient_count=len(request.addresses),
        run_id=claim.run_id,
        fence=claim.fence,
        worker_id=claim.worker_id,
        actor_id=claim.worker_id,
        correlation_id=claim.run_id,
    )


def _testing_recipient():
    """The Testing recipient every email goes to now; None in Production."""
    runtime = SystemConfiguration.objects.values("mode", "testing_recipient").get()
    return runtime["testing_recipient"] if runtime["mode"] == "testing" else None


def _facts(cohort, mode, *, public_origin):
    """What the cohort's emails say, from its request's recorded facts.

    The parish and campaign names are the cohort configuration's and the
    request's campaign's; ``public_origin`` is the preparing worker's own
    configured address, which the two links use.
    """
    if public_origin is None:
        raise PermissionError("Ministry leader emails need the public address.")
    require_work_order()
    request = LeaderEmailRequest.objects.select_related(
        "campaign__active_configuration"
    ).get(pk=cohort.request_id)
    campaign = request.campaign.active_configuration.name
    parish = Parish.objects.values_list("name", flat=True).get(
        configuration_id=cohort.configuration_id
    )
    return LeaderEmailFacts(
        parish=parish,
        campaign=campaign,
        public_origin=public_origin,
        mode=mode,
        testing_recipient=cohort.testing_recipient,
        ministries=recorded_ministries(request.recipients),
    )


def _mail_render(*, configuration_id, sender, reply_to, address, semantic_key, alert):
    """Compile one leader's email and its journal render."""
    from parishkit.stewardship.weekly_delivery import WeeklyDeliveryMail

    subject, html, text, routed = render_leader_email(alert, address)
    render = RenderInput(
        configuration_id=configuration_id,
        template_id=None,
        sender=sender,
        reply_to=reply_to,
        intended_recipients=(address,),
        routed_recipients=(routed,),
        subject=subject,
        html=html,
        text=text,
    )
    mail = WeeklyDeliveryMail(
        semantic_key, sender, reply_to, (routed,), subject, html, text
    )
    return mail, render


def _current_mail(store, cohort, address, semantic_key):
    """The kept content, under the current sender, once the mode is unchanged.

    A restore under review holds the email, as it holds campaign mail: what
    was delivered before the restore is not yet known. Pause and resume mail
    holds a Production email too while the current campaign's delivery is
    paused, since the pause holds every Production message not yet handed
    to the provider (admin-portal specification, "Live delivery pause"); it
    stays pending and is sent after Resume. The outbox state guard refuses
    the submission in SQL as well.
    """
    from parishkit.stewardship.weekly_delivery import WeeklyDeliveryMail

    route = _route(store)
    if route.sender is None or route.reply_to is None:
        raise FamilyDeliveryHeld("Ministry leader mail channel is not configured.")
    runtime = SystemConfiguration.objects.values(
        "restore_review_required", "current_campaign__delivery_paused"
    ).get()
    if runtime["restore_review_required"]:
        raise FamilyDeliveryHeld("A restore is under review.")
    if cohort.mode == "production" and runtime["current_campaign__delivery_paused"]:
        raise FamilyDeliveryHeld("Mail is paused.")
    if route.mode.value != cohort.mode:
        raise PermissionError("Ministry leader email mode changed.")
    recipient = LeaderEmailRecipient.objects.get(cohort=cohort, address=address)
    routed = cohort.testing_recipient if cohort.mode == "testing" else address
    render = RenderInput(
        configuration_id=route.configuration_id,
        template_id=None,
        sender=route.sender,
        reply_to=route.reply_to,
        intended_recipients=(address,),
        routed_recipients=(routed,),
        subject=recipient.subject,
        html=recipient.html,
        text=recipient.text,
    )
    mail = WeeklyDeliveryMail(
        semantic_key,
        route.sender,
        route.reply_to,
        (routed,),
        recipient.subject,
        recipient.html,
        recipient.text,
    )
    return mail, render


def _recipient_current(message):
    """Still a Ministry leader user, in the mode the email was prepared in.

    In Testing the Testing recipient must also still be the one the email
    was prepared for: a changed one cancels the unsent email, as a mode
    change does, rather than sending it to the old address.
    """
    recipient = (
        LeaderEmailRecipient.objects.select_related("cohort")
        .only("address", "cohort__testing_recipient")
        .get(outbox_id=message.pk)
    )
    runtime = SystemConfiguration.objects.values(
        "mode", "active_configuration_id", "testing_recipient"
    ).get()
    return (
        runtime["mode"] == message.mode
        and (
            message.mode != "testing"
            or recipient.cohort.testing_recipient == runtime["testing_recipient"]
        )
        and AddressRule.objects.filter(
            configuration_id=runtime["active_configuration_id"],
            email=recipient.address,
            role=ROLE,
        ).exists()
    )


def _submit(*args, **kwargs):
    """Submit through the private weekly report helper, by its module's name."""
    from parishkit.stewardship import family_delivery_process

    return family_delivery_process.submit_weekly(*args, **kwargs)


def leader_owner(public_origin=None):
    """The compiled owner; only the preparing worker needs ``public_origin``.

    The scheduler (which finds pending requests) and mail dispatch (which
    sends the kept content) never compile a link, so they use ``LEADER``.
    """
    return AlertOwner(
        purpose="leader_email",
        label="Ministry leader email",
        task_type=TASK_TYPE,
        namespace=NAMESPACE,
        source_field="request_id",
        cohort_model=LeaderEmailCohort,
        recipient_model=LeaderEmailRecipient,
        cohort_table="stewardship_leader_email_cohort",
        recipient_table="stewardship_leader_email_recipient",
        configured=_configured,
        pending_sources=_pending,
        source_exists=_source_exists,
        capture=_capture,
        alert=partial(_facts, public_origin=public_origin),
        mail_render=_mail_render,
        current_mail=_current_mail,
        recipient_current=_recipient_current,
        submit=_submit,
        testing_routed=True,
        keeps_content=True,
        held=_held,
        superseded=_superseded,
    )


LEADER = leader_owner()
