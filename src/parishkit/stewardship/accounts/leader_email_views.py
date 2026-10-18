"""The Users page's Email Ministry leaders section and its review (#1592).

The section sits below the ParishSoft Ministry leaders table on ``users``
(``user_views.users`` draws it with ``emails_context``): one table of the
Ministry leader users with their Last emailed outcome, whose Email selected
and Email all post the ticked addresses here, to ``user_leader_emails``
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
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    UserFacingError,
    UserFacingStale,
    unexpected_fields,
)
from parishkit.stewardship.web.tables import paginate

from .admin_editing import error_response, principal
from .authentication import runtime
from .leader_emails import (
    EMAIL_SORTING,
    SKIP_REASONS,
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
SALT = "stewardship-leader-emails-v1"
# A review lasts fifteen minutes, as every review does.
PREVIEW_SECONDS = 15 * 60
# At most this many addresses in one Email selected post: more than any page
# of the table can show.
CHOSEN_LIMIT = 2000
SCOPES = {"selected", "all"}
CHANGED = _(
    "Something changed since this review: the mode, the campaign, a user or "
    "a leader's Ministries. Nothing was sent."
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
    """The scope and chosen addresses, or refuse the form.

    ``scope`` is ``all`` or ``selected``; ticked rows post ``email``, which
    ``all`` ignores, since the table's form posts them with either button.
    """
    allowed = {"csrfmiddlewaretoken", "scope", "email"}
    if set(parameters) - allowed or any(
        len(values) != 1 for name, values in parameters.lists() if name != "email"
    ):
        raise unexpected_fields(parameters, allowed=allowed, multiple={"email"})
    scope = parameters.get("scope")
    emails = parameters.getlist("email")
    if scope not in SCOPES or len(emails) > CHOSEN_LIMIT:
        # No sources: the fields were allowed; a value is malformed.
        raise unexpected_fields()
    if scope == "all":
        return None
    try:
        chosen = [clean_email(email) for email in emails]
    except UserRefused:
        # No sources: the fields were allowed; an address is malformed.
        raise unexpected_fields() from None
    if not chosen:
        raise UserFacingError(
            _("No leader was chosen."),
            fix=_("Tick the leaders to email, or choose Email all."),
        )
    return chosen


def _recheck(request, service, actor):
    """Refuse an answer whose reader lost access while it was built."""
    current = principal(
        request, service, read_only=True, capability=Capability.MANAGE_USERS
    )
    if current.identity != actor.identity:
        raise PermissionError("Users reader changed.")


def _review(request, service, actor):
    """Answer Email selected or Email all with the review fragment."""
    from .user_reads import policy_identities, user_list
    from .user_views import _users

    chosen = _choice(request.POST)
    blocked = unavailable()
    if blocked is not None:
        raise UserFacingError(blocked, fix=_("Nothing was sent."))
    facts = read_facts(request.session.session_key)
    with read_transaction():
        configuration, records = _users(service)
        rows = user_list(records, policy_identities(records))
        runtime_row = SystemConfiguration.objects.only(
            "mode", "testing_recipient", "current_campaign_id"
        ).get()
        plan = plan_emails(leader_rows(rows), facts, chosen)
        request._stewardship_display_configuration = configuration
    testing = runtime_row.mode == "testing"
    token = None
    if plan.emailed:
        token = signing.dumps(
            {
                "actor": str(actor.identity),
                "key": str(uuid4()),
                "configuration": str(configuration.active_configuration.pk),
                "campaign": str(runtime_row.current_campaign_id),
                "mode": runtime_row.mode,
                "testing_recipient": runtime_row.testing_recipient if testing else None,
                "addresses": plan.addresses,
            },
            salt=SALT,
        )
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
            "preview": token,
        },
    )


def _confirm(request, actor):
    """Record the reviewed request once; answer with a short fragment.

    The dialog redraws the Users page from its own address afterwards, so
    the answer only has to be a page the dialog accepts. A replay of the
    same review (a double click) records nothing more. A review whose
    configuration, campaign, mode or leaders changed since is refused by
    the request's guard, and nothing is sent.
    """
    allowed = {"csrfmiddlewaretoken", "action", "preview"}
    if set(request.POST) - allowed or any(
        len(values) != 1 for _, values in request.POST.lists()
    ):
        raise unexpected_fields(request.POST, allowed=allowed)
    intent = signing.loads(
        request.POST.get("preview", ""), salt=SALT, max_age=PREVIEW_SECONDS
    )
    if intent["actor"] != str(actor.identity):
        raise PermissionError("The review belongs to another Administrator.")
    try:
        _, created = request_emails(
            actor.identity,
            key=UUID(intent["key"]),
            addresses=intent["addresses"],
            configuration_id=UUID(intent["configuration"]),
            campaign_id=UUID(intent["campaign"]),
            mode=intent["mode"],
            testing_recipient=intent["testing_recipient"],
        )
    except IntegrityError:
        raise UserFacingStale(CHANGED, fix=CHANGED_FIX, reason="stale_page") from None
    count = len(intent["addresses"])
    return HttpResponse(
        f'<p data-leader-emails-queued="{count}" data-created="{int(created)}"></p>'
    )


@require_POST
def user_leader_emails(request):
    """Review emailing the chosen Ministry leaders, or record the reviewed send.

    The review (Email selected, Email all) reads each leader's Ministries
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
