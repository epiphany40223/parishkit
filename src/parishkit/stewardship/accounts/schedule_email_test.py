"""Send a test of a scheduled email's unsaved edit (#446, slice 3c).

New and Edit scheduled email's "This schedule's email" fields
(``schedule_email``) offer **Send a test of this text**. The page script
posts the fields as they are now, with the email chosen in Email to send, to
this POST-only action, without a page load. The text is checked as a save
checks it (``ScheduleEmailForm``, the Pages and emails editor's rules)
against the chosen email's mail type, which is the page's: Email to send
only offers emails of the page's type. It is then sent as the one test
Preview and test email sends (``campaign_mail.request_edited``): a fictional
sample, to the Testing recipient only, under the same SQL admission, limits
and audit.

Nothing is saved: no configuration request, content record or schedule
change. No Family code, link or sealed credential is read or made; the
sample's Family values are the fixed fictional ones.

The answer is JSON for the page script: ``{"queued": true}``; ``400`` with
``{"errors": [...]}`` (each ``{"field", "message"}``) for text that would not
save; ``409`` with ``"unknown": true`` and a refusal while an earlier test's
result is uncertain, so the page shows the acknowledgment; or any other
refusal (``web.refusals``), which the status line shows in words.
"""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    Refusal,
    UserFacingError,
    UserFacingStale,
    unexpected_fields,
    with_codes,
    with_default_code,
    with_reason,
)

from .admin_editing import error_response, principal
from .authentication import runtime
from .campaign_mail import TestPending, TestUncertain, request_edited
from .content_views import _records
from .schedule_email import FIELDS, NO_EMAIL, ScheduleEmailForm, emails_of
from .schedule_entry_views import ERRORS, _recheck
from .schedule_forms import KINDS
from .schedule_reads import schedule_state

# The chosen email (Email to send) and the uncertain-test acknowledgment.
CHOSEN = "template_version"
ACKNOWLEDGE = "acknowledge_unknown"
POSTED = {CHOSEN, ACKNOWLEDGE, *FIELDS}


def _chosen(records, value):
    """The chosen saved email's ID and mail type, or a refusal.

    The mail type is the email's own, one of the schedulable types; a test
    page's script only offers emails of the page's type.
    """
    try:
        identifier = str(UUID(value))
    except (TypeError, ValueError):
        raise UserFacingError(NO_EMAIL, fix=_("Nothing was sent.")) from None
    for kind in KINDS:
        if identifier in emails_of(records, kind):
            return identifier, kind
    raise UserFacingStale(
        _("The email chosen in Email to send is no longer saved."),
        fix=_("Nothing was sent. Reload the page, then choose an email again."),
    )


def _errors(form):
    """A refused text's messages, each with its field's label (or none)."""
    return [
        {
            "field": None if name == "__all__" else str(form.fields[name].label),
            "message": str(message),
        }
        for name, messages in form.errors.items()
        for message in messages
    ]


def _answer(body, status=200):
    """A JSON answer the page script reads, kept as is by the error pages."""
    response = JsonResponse(body, status=status)
    response.stewardship_safe_error = True
    # The refusal log's code for this page's own refusal (#1444).
    with_default_code(response)
    return response


def _send(request, campaign_id):
    """Check the posted text and queue its test; returns the JSON answer.

    The chosen email and its mail type are read in a read transaction, then
    ``request_edited`` takes the work lock and repeats every admission check
    (the configuration, the campaign's state, the Admin and the credential)
    as the saved email's test does.
    """
    service = runtime()
    principal(request, service)
    filters(request.GET, allowed=set())
    if (
        request.FILES
        or set(request.POST) - {*POSTED, "csrfmiddlewaretoken"}
        or any(len(values) != 1 for _name, values in request.POST.lists())
    ):
        raise unexpected_fields(
            request.POST, request.FILES, allowed={*POSTED, "csrfmiddlewaretoken"}
        )
    with read_transaction():
        state, campaign, _editable = schedule_state(service, campaign_id, page=True)
        records = _records(state[0], campaign.pk)
    chosen, kind = _chosen(records, request.POST.get(CHOSEN))
    form = ScheduleEmailForm(request.POST, kind="email", slot=kind)
    if not form.is_valid():
        # The form's own fields and codes reach the refusal log through the
        # Admin form base; ``invalid`` says the text itself was refused.
        return with_codes(_answer({"errors": _errors(form)}, status=400), "invalid")
    values = form.values(campaign_id=campaign.pk, slot=kind)
    text = {key: values[key] for key in ("subject", "html", "text")}
    try:
        request_edited(
            request,
            service,
            campaign.pk,
            UUID(chosen),
            text,
            acknowledge_unknown=request.POST.get(ACKNOWLEDGE) == "on",
        )
    except TestPending:
        raise UserFacingStale(
            _("A test email is already on its way."),
            fix=_("Wait a minute for it to finish, then send another."),
            reason="test_pending",
        ) from None
    except TestUncertain:
        refusal = Refusal(
            _("An earlier test may have arrived: its result is uncertain."),
            fix=_(
                "Check the Testing mailbox first. To send another test anyway, "
                "tick the confirmation below."
            ),
        )
        return with_reason(
            _answer({"unknown": True, "refusal": refusal.as_dict()}, status=409),
            "test_result_uncertain",
        )
    except ObjectDoesNotExist:
        # The chosen email, or the mail settings, are gone from the applied
        # configuration since the read above: as Preview and test email
        # answers it.
        raise LookupError("Campaign test configuration is unavailable.") from None
    return _answer({"queued": True})


@require_http_methods(["POST"])
def schedule_email_test(request, campaign_id):
    """Send a test of a scheduled email's unsaved text; nothing is saved."""
    try:
        response = _send(request, campaign_id)
        _recheck(request, runtime())
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)
