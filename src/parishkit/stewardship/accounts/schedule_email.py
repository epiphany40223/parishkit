"""Edit a schedule's own email on New and Edit scheduled email (#446, slice 3).

New and Edit scheduled email offer **Edit this schedule's email** under Email
to send. Ticking it shows the email's subject, HTML source and plain text,
filled in from the chosen email, with **Reset to the default text**. The
fields are the Pages and emails editor's (``ContentForm``: the same cleaning,
placeholders, hosted files and Family-access rules), without its removal
control, its visual editor or the campaign banner choice, which stay on Pages
and emails.

A changed email is always saved as a new email that only this schedule
sends: the change adds that content record, and the schedule's
``template_version`` and ``subject`` point at it, reviewed and applied with
the rest of the schedule's change in one request. On New with Repeat, every
reminder the rule adds sends that one record. The email the schedule
sent before is left exactly as it is, so every other schedule that sends it
keeps it. Text left as the chosen email's adds nothing.

Nothing here touches Family codes, links or their sealed credentials; an
invitation or reminder still needs ``{{ family_code }}`` and ``{{ family_url }}``
in both parts, as the content rules require.
"""

from uuid import uuid4

from django import forms
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.web.admin_forms import AdminForm

from . import setup_help
from .content_forms import ContentForm, default_values, text_is_generated
from .schedule_forms import KINDS

# The page's checkbox, and the email fields it shows (unprefixed, so the
# shared "Generate plain text from HTML" script in ui-v1.js serves them as it
# serves the content editors).
EDIT = "edit_email"
FIELDS = ("subject", "html", "generate_text", "text")
# New scheduled email's posted mail type (schedule_entry_views' form prefix).
KIND = "schedule-kind"
# Why "Send a test of this text" waits (#446): the page draws it for its
# script, and the send refuses with it, so the two always read the same.
NO_EMAIL = _("Choose an email to send first.")
NEW_TEXT_HELP = _(
    "For an Initial invitation or a Reminder, both the formatted and "
    "plain-text versions must include {{ family_code }} and {{ family_url }}, "
    "and the subject must include neither. The text is checked against the "
    "mail type chosen above."
)


class EmailToggle(AdminForm):
    """The "Edit this schedule's email" checkbox.

    It only shows the email's fields, so the page's change gate ignores it
    (``data-change-ignore``) and counts the fields instead: ticking it
    without editing the email leaves Review changes unavailable.
    """

    edit_email = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"data-change-ignore": ""}),
        label=_("Edit this schedule's email"),
        help_text=_(
            "Change the subject or text for this scheduled email only. Saving "
            "makes a new email just for it; the email chosen above stays as it "
            "is for every other schedule."
        ),
    )


class ScheduleEmailForm(ContentForm):
    """The content editor's email fields, without removal or a version field.

    The page posts its own ``base_digest``, which the schedule review checks.
    """

    base_digest = None
    clear = None


def _source(values):
    """One email's editor values, as the page script fills the fields."""
    return {
        "subject": values["subject"],
        "html": values["html"],
        "text": values["text"],
        "generate": text_is_generated(values),
    }


def emails_of(records, kind):
    """The campaign's saved emails of mail type ``kind``, by ID."""
    return {
        row["id"]: row["values"]
        for row in records
        if (row["values"]["kind"], row["values"]["slot"]) == ("email", kind)
    }


def editor(data, records, schedule, campaign_id):
    """The checkbox, the email form and the page script's email texts.

    ``data`` is the posted page or None; ``records`` the campaign's content.
    On Edit, ``schedule`` is the saved schedule, whose mail type is fixed; on
    New it is None and the mail type is the one posted (``schedule-kind``), so the
    fields are checked against the type the Admin chose. The email form is
    bound only while the checkbox is ticked (``form.is_bound``), so an
    unticked page never validates or saves email text. Unbound, Edit's starts
    from the schedule's saved email (the built-in default when that email is
    no longer saved) and New's is empty until the page script fills it.
    ``sources`` holds every email of the mail types the page offers and each
    type's default (``defaults``), which the page script fills the fields
    from.
    """
    toggle = EmailToggle(data)
    editing = data is not None and toggle.is_valid() and toggle.cleaned_data[EDIT]
    if schedule is not None:
        kind = schedule["values"]["kind"]
        kinds = (kind,)
    else:
        posted = data.get(KIND) if data is not None else None
        kind = posted if posted in KINDS else None
        kinds = KINDS
    defaults = {
        each: default_values("email", each, campaign_id=campaign_id) for each in kinds
    }
    initial = {}
    if schedule is not None:
        saved = emails_of(records, kind).get(
            schedule["values"]["template_version"], defaults[kind]
        )
        initial = {
            "subject": saved["subject"],
            "html": saved["html"],
            "text": saved["text"],
            "generate_text": text_is_generated(saved),
        }
    form = ScheduleEmailForm(
        data if editing else None, kind="email", slot=kind, initial=initial
    )
    if schedule is None:
        # New's mail type can change on the page, so its plain-text help
        # states the invitation and reminder rule for every type (Edit's
        # comes from ContentForm for its fixed type).
        form.fields["text"].help_text = NEW_TEXT_HELP
    # The content editors' field help, long help behind its "i".
    setup_help.apply(form, setup_help.CONTENT)
    sources = {
        "emails": {
            identifier: _source(values)
            for each in kinds
            for identifier, values in emails_of(records, each).items()
        },
        "defaults": {each: _source(values) for each, values in defaults.items()},
    }
    return toggle, form, sources


def test_context(admitted, campaign_id):
    """What the page's "Send a test of this text" needs (#446, slice 3c).

    ``admitted`` is whether the campaign admits test mail now
    (``campaign_mail.admits_test_mail``); while it does not, the button is
    drawn unavailable with that reason. ``unknown`` is whether an earlier
    test's result is uncertain, so the page draws the acknowledgment the
    send needs. The send itself is ``schedule_email_test``.
    """
    from .campaign_mail import NOT_OPEN
    from .campaign_mail_models import CampaignMailTest

    return {
        "url": reverse("admin:schedule_email_test"),
        "no_email": NO_EMAIL,
        "closed": None if admitted else NOT_OPEN,
        "unknown": CampaignMailTest.objects.filter(
            campaign_id=campaign_id, state="delivery_unknown"
        ).exists(),
    }


def refuse(form, message, code):
    """Refuse the edited email as a whole, at its HTML source.

    A refusal of the whole email (the configuration refusing it, or its
    review being too large) is put on a field, never only on the form, so
    the review dialog's in-place answer marks the field (aria-invalid),
    places the message before it and links the error summary to it.
    ``code`` is its closed error code, which the refusal log records.
    """
    form.add_error("html", forms.ValidationError(message, code=code))


def copy(form, records, chosen, *, campaign_id, kind):
    """The content add for an edited email, or None when nothing changed.

    ``chosen`` is the email the page's list names. Text equal to it (after
    the same cleaning a save applies) adds nothing; anything else is a new
    record with a new ID, never a change to ``chosen``.
    """
    values = form.values(campaign_id=campaign_id, slot=kind)
    if emails_of(records, kind).get(chosen) == values:
        return None
    return {
        "operation": "add",
        "section": "content",
        "id": str(uuid4()),
        "values": values,
    }
