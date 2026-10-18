"""The Users page's Email Ministry leaders section and its review (#1592).

The section sits below the ParishSoft Ministry leaders table on ``users``
(``user_views.users`` draws it with ``emails_context``): one table of the
Ministry leader users with their Last emailed outcome, whose Email selected
posts the ticked addresses here, to ``user_leader_emails``
(``/admin/users/leader-emails/``). It answers with the **Email Ministry
leaders** review, a fragment the page shows in the shared review dialog
(UX conventions, "Review dialog"). The dialog's Apply posts the signed
review back here (``action=confirm``), which records one request; the
background engine prepares and sends the emails (``jobs.leader_email_owner``).
Nothing is ever sent from a page load, a review or any other action.

Every route is Administrator-only (``Capability.MANAGE_USERS``) and checks
that again after building its answer. Each leader's Ministries and counts
are read through the ``stewardship_leader_emails_v1`` definer
(``leader_emails.read_facts``), so a posted address only chooses among the
Ministry leader users read now; the request's guard reads them again itself.

``pk-admin users email-leaders`` (#1622, ``admin_users``) builds the same
review and records the same request through ``review_facts``,
``review_plan``, ``review_token`` and ``apply_review``, so the page and the
command line sign one binding and refuse alike.
"""

from uuid import UUID, uuid4

from django.core import signing
from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.jobs.leader_email_models import LeaderEmailRequest
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    UserFacingError,
    UserFacingStale,
    load_preview,
    unexpected_fields,
)
from parishkit.stewardship.web.tables import paginate

from .admin_editing import error_response, principal
from .authentication import runtime
from .campaign_mail import recipient_digest
from .leader_emails import (
    EMAIL_SORTING,
    SKIP_REASONS,
    LeaderEmailsChanged,
    leader_rows,
    plan_emails,
    read_facts,
    request_emails,
)
from .policy import Capability
from .runtime_models import SystemConfiguration
from .user_changes import UserRefused, clean_email

# The Email Ministry leaders table's query-string prefix, beside the users
# table's ``users_`` and the leaders table's ``leaders_``.
PREFIX = "emails_"
# A review lasts fifteen minutes (web.refusals.PREVIEW_MAX_AGE), as every
# review does.
SALT = "stewardship-leader-emails-v1"
# At most this many addresses in one Email selected post: more than any page
# of the table can show.
CHOSEN_LIMIT = 2000
CHANGED = _(
    "Something changed since this review: the mode, the campaign, a user, or "
    "a leader's Ministries or open requests. Nothing was sent."
)
CHANGED_FIX = _("Close this, then choose the leaders and review again.")
NO_CAMPAIGN = _(
    "There is no current campaign with Ministries, so there is nothing to tell "
    "Ministry leaders yet."
)
ARCHIVED = _(
    "The campaign is archived, so its Ministry leaders no longer follow up its "
    "requests and no email can be sent."
)
PAUSED = _(
    "Mail is paused, so no Ministry leader email can be queued now. Nothing was sent."
)
PAUSED_FIX = _(
    "Resume mail on Pause and resume mail first, then choose the leaders and "
    "review again."
)
# A replayed Apply (a double click) that lands after a pause: the first
# Apply's emails are queued already.
PAUSED_QUEUED = _(
    "Mail is paused. The emails this review queued at its first Apply are "
    "held and will be sent after mail is resumed. Nothing new was queued."
)
PAUSED_QUEUED_FIX = _("Resume mail on Pause and resume mail to send them.")


def unavailable():
    """Why no email can be sent now, or None while one can.

    The emails need a current campaign with Ministries that is not archived:
    an archived campaign's leaders no longer work it (#527).
    """
    runtime_row = (
        SystemConfiguration.objects.select_related(
            "current_campaign__active_configuration"
        )
        .only(
            "current_campaign__state",
            "current_campaign__active_configuration__values",
        )
        .first()
    )
    campaign = runtime_row and runtime_row.current_campaign
    if campaign is None or "ministry" not in (
        campaign.active_configuration.values.get("modules") or []
    ):
        return NO_CAMPAIGN
    if campaign.state == "archived":
        return ARCHIVED
    return None


def refuse_while_paused(key=None):
    """Refuse a review or Apply while the current campaign's mail is paused.

    Pause and resume mail holds every Production message not yet handed to
    the provider (admin-portal specification, "Live delivery pause"), so
    nothing new is queued meanwhile; the request's guard refuses it in SQL
    too, and a prepared email waits for Resume. Testing mail is never held,
    as for campaign mail. ``key`` is an Apply's request key: when that
    request was recorded before the pause (a replayed Apply), the refusal
    says its emails are queued and held rather than that nothing was sent.
    """
    if SystemConfiguration.objects.filter(
        mode="production", current_campaign__delivery_paused=True
    ).exists():
        if key is not None and LeaderEmailRequest.objects.filter(pk=key).exists():
            raise UserFacingError(PAUSED_QUEUED, fix=PAUSED_QUEUED_FIX)
        raise UserFacingError(PAUSED, fix=PAUSED_FIX)


def emails_context(rows, paging, carry):
    """The Email Ministry leaders table's part of the Users page context.

    ``rows`` are the users table's own rows (``user_reads.user_list``), so
    this reads only each address's latest leader email; ``carry`` keeps the
    other tables' places in this table's links. An unknown sort is refused
    before anything is read.
    """
    EMAIL_SORTING.parse(paging, PREFIX)
    blocked = unavailable()
    table = paginate(
        leader_rows(rows),
        paging,
        prefix=PREFIX,
        carry=carry,
        sorting=EMAIL_SORTING,
    )
    return {
        "emails": table,
        "emails_unavailable": blocked,
        "emails_url": reverse("admin:user_leader_emails"),
    }


def _choice(parameters):
    """The chosen addresses, or refuse the form.

    The table's form posts the ticked rows as ``email`` and nothing else
    (there is no Email all on the page; ``pk-admin users email-leaders --all``
    plans every leader without this form).
    """
    allowed = {"csrfmiddlewaretoken", "email"}
    if set(parameters) - allowed or any(
        len(values) != 1 for name, values in parameters.lists() if name != "email"
    ):
        raise unexpected_fields(parameters, allowed=allowed, multiple={"email"})
    emails = parameters.getlist("email")
    if len(emails) > CHOSEN_LIMIT:
        # No sources: the fields were allowed; a value is malformed.
        raise unexpected_fields()
    try:
        chosen = [clean_email(email) for email in emails]
    except UserRefused:
        # No sources: the fields were allowed; an address is malformed.
        raise unexpected_fields() from None
    if not chosen:
        raise UserFacingError(
            _("No leader was chosen."),
            fix=_("Tick the leaders to email."),
        )
    return chosen


def _recheck(request, service, actor):
    """Refuse an answer whose reader lost access while it was built."""
    current = principal(
        request, service, read_only=True, capability=Capability.MANAGE_USERS
    )
    if current.identity != actor.identity:
        raise PermissionError("Users reader changed.")


def review_facts(session_key):
    """Refuse a review that cannot be sent now, else read the leaders' facts.

    The page's review and ``pk-admin users email-leaders --preview`` (#1622)
    both start here: no current campaign with Ministries, an archived
    campaign or paused mail is refused (``UserFacingError``) before
    anything is read; otherwise the definer reads every Ministry leader
    user's facts under ``session_key`` (the browser's or the command's own
    Admin session) and records its count-only audit event.
    """
    blocked = unavailable()
    if blocked is not None:
        raise UserFacingError(blocked, fix=_("Nothing was sent."))
    refuse_while_paused()
    return read_facts(session_key)


def review_plan(service, facts, chosen):
    """Plan the emails in one read-only snapshot of the users and the mode.

    Returns ``(plan, configuration, runtime_row)``: ``plan_emails``' answer
    for the ``chosen`` addresses (None for every leader), the users'
    configuration ``user_views._users`` read, and the runtime row with the
    mode, Testing recipient and current campaign the review is signed for.
    """
    from .user_reads import policy_identities, user_list
    from .user_views import _users

    with read_transaction():
        configuration, records = _users(service)
        rows = user_list(records, policy_identities(records))
        runtime_row = SystemConfiguration.objects.only(
            "mode", "testing_recipient", "current_campaign_id"
        ).get()
        plan = plan_emails(leader_rows(rows), facts, chosen)
    return plan, configuration, runtime_row


def review_token(actor, configuration, runtime_row, plan):
    """The signed review one Apply records, or None with nobody to email.

    The page and the command line sign the same intent, bound to the
    Administrator (not the channel), so either may apply the other's.
    """
    if not plan.emailed:
        return None
    testing = runtime_row.mode == "testing"
    return signing.dumps(
        {
            "actor": str(actor.identity),
            "key": str(uuid4()),
            "configuration": str(configuration.active_configuration.pk),
            "campaign": str(runtime_row.current_campaign_id),
            "mode": runtime_row.mode,
            # The token is signed, not encrypted, so it binds the Testing
            # recipient by digest and never carries the address itself.
            "recipient": _recipient_binding(
                runtime_row.testing_recipient if testing else None
            ),
            "addresses": plan.addresses,
            # What the review showed each leader's email would state;
            # Apply is refused when the guard works out anything else.
            "facts": plan.digest,
        },
        salt=SALT,
    )


def _review(request, service, actor):
    """Answer Email selected with the review fragment."""
    chosen = _choice(request.POST)
    facts = review_facts(request.session.session_key)
    plan, configuration, runtime_row = review_plan(service, facts, chosen)
    request._stewardship_display_configuration = configuration
    testing = runtime_row.mode == "testing"
    return render(
        request,
        "stewardship/user-leader-emails.html",
        {
            "emailed": plan.emailed,
            "skipped": [
                item | {"reason_label": SKIP_REASONS[item["reason"]]}
                for item in plan.skipped
            ],
            "remaining": plan.remaining,
            "testing_recipient": runtime_row.testing_recipient if testing else None,
            "preview": review_token(actor, configuration, runtime_row, plan),
        },
    )


def _recipient_binding(testing_recipient):
    """The token's binding of the Testing recipient: its digest, or "" in Production."""
    return recipient_digest(testing_recipient) if testing_recipient else ""


def _current_recipient(intent):
    """The Testing recipient to record for a reviewed Apply, or refuse it as stale.

    The review signed only the recipient's digest, so Apply reads the
    current Testing recipient (None in Production) and refuses the review
    as changed when its digest differs from the signed one. The request's
    guard still checks the address against the runtime row.
    """
    runtime_row = SystemConfiguration.objects.only("mode", "testing_recipient").get()
    current = runtime_row.testing_recipient if runtime_row.mode == "testing" else None
    if intent.get("recipient") != _recipient_binding(current):
        raise UserFacingStale(CHANGED, fix=CHANGED_FIX, reason="stale_page")
    return current


def apply_review(actor, token):
    """Record the signed review ``token`` once; returns ``(intent, created)``.

    The page's Apply and ``pk-admin users email-leaders --confirm`` (#1622)
    both end here. An expired review is the shared stale refusal and a
    tampered one ``BadSignature``; another Administrator's review is a
    ``PermissionError``; paused mail is refused (``UserFacingError``) before
    anything is recorded. A review whose configuration, campaign, mode,
    leaders, Ministries or counts changed since is ``UserFacingStale``
    (``CHANGED``), and nothing is recorded; so is one whose Testing
    recipient changed (``_current_recipient``). A replay of the same review
    records nothing more (``created`` is false).
    """
    intent = load_preview(token, salt=SALT)
    if intent["actor"] != str(actor.identity):
        raise PermissionError("The review belongs to another Administrator.")
    refuse_while_paused(UUID(intent["key"]))
    testing_recipient = _current_recipient(intent)
    try:
        _, created = request_emails(
            actor.identity,
            key=UUID(intent["key"]),
            addresses=intent["addresses"],
            configuration_id=UUID(intent["configuration"]),
            campaign_id=UUID(intent["campaign"]),
            mode=intent["mode"],
            testing_recipient=testing_recipient,
            reviewed=intent["facts"],
        )
    except (IntegrityError, LeaderEmailsChanged):
        raise UserFacingStale(CHANGED, fix=CHANGED_FIX, reason="stale_page") from None
    return intent, created


def _confirm(request, actor):
    """Record the reviewed request once; answer with a short fragment.

    The dialog redraws the Users page from its own address afterwards, so
    the answer only has to be a page the dialog accepts. A replay of the
    same review (a double click) records nothing more. A review whose
    configuration, campaign, mode or leaders changed since is refused by
    the request's guard, one whose Testing recipient changed is refused
    by ``_current_recipient``, and one whose Ministries or counts the guard
    works out differently from the review is refused here; either way nothing is
    sent. While mail is paused Apply is refused with its plain reason, and
    an expired review gets the shared expired-review refusal.
    """
    allowed = {"csrfmiddlewaretoken", "action", "preview"}
    if set(request.POST) - allowed or any(
        len(values) != 1 for _, values in request.POST.lists()
    ):
        raise unexpected_fields(request.POST, allowed=allowed)
    intent, created = apply_review(actor, request.POST.get("preview", ""))
    count = len(intent["addresses"])
    return HttpResponse(
        f'<p data-leader-emails-queued="{count}" data-created="{int(created)}"></p>'
    )


@require_POST
def user_leader_emails(request):
    """Review emailing the chosen Ministry leaders, or record the reviewed send.

    The review (Email selected) reads each leader's Ministries
    and counts now and signs who is emailed for one Apply; Apply
    (``action=confirm``) records it. Neither sends anything itself.
    """
    from .user_views import ERRORS

    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        filters(request.GET, allowed=set())
        if request.FILES:
            raise unexpected_fields(request.FILES, allowed=())
        if request.POST.get("action") == "confirm":
            response = _confirm(request, actor)
        else:
            response = _review(request, service, actor)
        _recheck(request, service, actor)
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)
