"""Edit a schedule's own email on Edit scheduled email (#446, slice 3).

Edit scheduled email offers **Edit this schedule's email** under Email to
send. Ticking it shows the email's subject, HTML source and plain text,
filled in from the chosen email, with **Reset to the default text**. The
fields are the Pages and emails editor's (``ContentForm``: the same cleaning,
placeholders, hosted files and Family-access rules), without its removal
control, its visual editor or the campaign banner choice, which stay on Pages
and emails.

A changed email is always saved as a new email that only this schedule
sends: the change adds that content record, and the schedule's
``template_version`` and ``subject`` point at it, reviewed and applied with
the rest of the schedule's change in one request. The email the schedule
sent before is left exactly as it is, so every other schedule that sends it
keeps it. Text left as the chosen email's adds nothing.

Nothing here touches Family codes, links or their sealed credentials; an
invitation or reminder still needs ``{{ family_code }}`` and ``{{ family_url }}``
in both parts, as the content rules require.
"""

from uuid import uuid4

from django import forms
from django.utils.translation import gettext_lazy as _

from . import setup_help
from .content_forms import ContentForm, default_values, text_is_generated

# The page's checkbox, and the email fields it shows (unprefixed, so the
# shared "Generate plain text from HTML" script in ui-v1.js serves them as it
# serves the content editors).
EDIT = "edit_email"
FIELDS = ("subject", "html", "generate_text", "text")


class EmailToggle(forms.Form):
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
            "Change the subject or text for this schedule only. Saving makes a "
            "new email that only this schedule sends; the email it sends now "
            "stays as it is for any other schedule."
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

    ``data`` is the posted page or None; ``records`` the campaign's content and
    ``schedule`` the saved schedule being edited. The email form is bound only
    while the checkbox is ticked (``form.is_bound``), so an unticked page
    never validates or saves email text. Unbound, it starts from the
    schedule's saved email (the built-in default when that email is no longer
    saved). ``sources`` holds every email of this mail type and the default,
    which the page script fills the fields from.
    """
    kind = schedule["values"]["kind"]
    toggle = EmailToggle(data)
    editing = data is not None and toggle.is_valid() and toggle.cleaned_data[EDIT]
    emails = emails_of(records, kind)
    default = default_values("email", kind, campaign_id=campaign_id)
    saved = emails.get(schedule["values"]["template_version"], default)
    form = ScheduleEmailForm(
        data if editing else None,
        kind="email",
        slot=kind,
        initial={
            "subject": saved["subject"],
            "html": saved["html"],
            "text": saved["text"],
            "generate_text": text_is_generated(saved),
        },
    )
    # The content editors' field help, long help behind its "i".
    setup_help.apply(form, setup_help.CONTENT)
    sources = {
        "emails": {
            identifier: _source(values) for identifier, values in emails.items()
        },
        "default": _source(default),
    }
    return toggle, form, sources


def refuse(form, message):
    """Refuse the edited email as a whole, at its HTML source.

    A refusal of the whole email (the configuration refusing it, or its
    review being too large) is put on a field, never only on the form, so
    the review dialog's in-place answer marks the field (aria-invalid),
    places the message before it and links the error summary to it.
    """
    form.add_error("html", message)


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
