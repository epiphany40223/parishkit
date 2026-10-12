"""Edit a schedule's own email on New and Edit scheduled email (#446, slice 3).

Without a database: what the page's editor starts from, when it is in use,
what it offers the page script, and when a save adds a new email. The views'
database path is in database/test_schedule_entry_postgresql.py and the page
script in browser/test_schedule_list.py.
"""

import json

from django.http import QueryDict
from django.template.loader import render_to_string

from parishkit.stewardship.accounts import schedule_email
from parishkit.stewardship.accounts.content_forms import default_values
from parishkit.stewardship.accounts.schedule_entry_views import (
    _fields,
    _summary,
    entry_form,
)

from .campaign_factory import campaign, schedule
from .content_factory import content

OWNER = campaign()
CAMPAIGN = OWNER["id"]
CODE = " {{ family_code }} {{ family_url }}"


def setup():
    """Two reminder emails and an invitation; Reminder 1 sends the first."""
    default = default_values("email", "reminder", campaign_id=CAMPAIGN)
    records = [
        {"id": "a" * 8 + "-0000-4000-8000-000000000001", "values": default},
        content(CAMPAIGN, kind="email", slot="reminder", subject="Second"),
        content(CAMPAIGN, kind="email", slot="initial", subject="Invitation"),
    ]
    row = schedule(
        CAMPAIGN,
        kind="reminder",
        date="2054-10-10",
        template_version=records[0]["id"],
    )
    return records, row


def posted(**fields):
    """An Edit page's post with the email fields ``fields``."""
    data = QueryDict(mutable=True)
    data.update(fields)
    return data


def test_the_editor_starts_unused_from_the_saved_email():
    """Unticked, nothing is bound; the fields hold the schedule's email."""
    records, row = setup()
    toggle, form, sources = schedule_email.editor(None, records, row, CAMPAIGN)
    assert not toggle.is_bound and not form.is_bound
    saved = records[0]["values"]
    assert form["subject"].value() == saved["subject"]
    assert form["html"].value() == saved["html"]
    assert form["generate_text"].value() is True
    # No removal control and no version field of its own.
    assert "clear" not in form.fields and "base_digest" not in form.fields
    # Every reminder email and the default, never another mail type's.
    assert set(sources["emails"]) == {records[0]["id"], records[1]["id"]}
    assert set(sources["defaults"]) == {"reminder"}
    assert sources["defaults"]["reminder"]["subject"] == saved["subject"]
    assert sources["emails"][records[1]["id"]]["subject"] == "Second"


def test_an_unticked_post_never_reads_the_email_fields():
    """Only a ticked box binds the email form, so nothing else validates it."""
    records, row = setup()
    _toggle, form, _sources = schedule_email.editor(
        posted(subject=""), records, row, CAMPAIGN
    )
    assert not form.is_bound


def test_an_email_no_longer_saved_starts_from_the_default():
    """A schedule whose email is gone opens on the built-in default."""
    records, row = setup()
    row["values"]["template_version"] = "f" * 8 + "-0000-4000-8000-000000000009"
    _toggle, form, _sources = schedule_email.editor(None, records[1:], row, CAMPAIGN)
    default = default_values("email", "reminder", campaign_id=CAMPAIGN)
    assert form["subject"].value() == default["subject"]


def test_changed_text_is_a_new_email_and_unchanged_text_adds_nothing():
    """A copy has a new ID and this campaign's mail type; the same text none."""
    records, row = setup()
    saved = records[0]["values"]
    same = posted(
        edit_email="on",
        subject=saved["subject"],
        html=saved["html"],
        generate_text="on",
    )
    _toggle, form, _sources = schedule_email.editor(same, records, row, CAMPAIGN)
    assert form.is_valid(), form.errors
    arguments = {"campaign_id": CAMPAIGN, "kind": "reminder"}
    assert schedule_email.copy(form, records, records[0]["id"], **arguments) is None
    # The same text chosen against the other email is a change.
    added = schedule_email.copy(form, records, records[1]["id"], **arguments)
    assert added["operation"] == "add" and added["section"] == "content"
    changed = same.copy()
    changed["subject"] = "A gentle reminder"
    _toggle, form, _sources = schedule_email.editor(changed, records, row, CAMPAIGN)
    assert form.is_valid(), form.errors
    added = schedule_email.copy(form, records, records[0]["id"], **arguments)
    assert added["id"] not in {record["id"] for record in records}
    values = added["values"]
    assert (values["campaign_id"], values["kind"], values["slot"]) == (
        CAMPAIGN,
        "email",
        "reminder",
    )
    assert values["subject"] == "A gentle reminder"
    # The email it starts from is untouched.
    assert records[0]["values"] == saved


def test_the_content_rules_apply_and_their_errors_reach_the_summary():
    """A reminder without its Family code is refused, as on Pages and emails."""
    records, row = setup()
    data = posted(
        edit_email="on",
        subject="Reminder",
        html="<p>Please respond.</p>",
        generate_text="on",
    )
    _toggle, form, _sources = schedule_email.editor(data, records, row, CAMPAIGN)
    assert not form.is_valid()
    assert "{{ family_code }}" in " ".join(form.non_field_errors())
    blank = data.copy()
    blank["subject"] = ""
    _toggle, form, _sources = schedule_email.editor(blank, records, row, CAMPAIGN)
    entry = entry_form(None, records, [row], row)
    summary = _summary(entry, None, form)
    assert {"message": "This field is required.", "field_id": "id_subject"} in [
        {"message": str(item["message"]), "field_id": item["field_id"]}
        for item in summary
    ]
    # An unused editor adds nothing to the summary.
    _toggle, unused, _sources = schedule_email.editor(None, records, row, CAMPAIGN)
    assert _summary(entry, None, unused) == []


def test_new_and_edit_both_post_the_email_fields():
    """Both pages may post the schedule's own email."""
    edit, new = _fields(False), _fields(True)
    for name in ("edit_email", "subject", "html", "generate_text", "text"):
        assert name in edit and name in new


def test_new_starts_empty_and_offers_every_mail_type():
    """New has no saved email: empty fields, every type's emails and default."""
    records, _row = setup()
    toggle, form, sources = schedule_email.editor(None, records, None, CAMPAIGN)
    assert not toggle.is_bound and not form.is_bound
    assert form["subject"].value() is None and form["html"].value() is None
    assert set(sources["emails"]) == {record["id"] for record in records}
    assert set(sources["defaults"]) == {
        "initial",
        "reminder",
        "daily_digest",
        "weekly_digest",
    }
    assert sources["emails"][records[2]["id"]]["subject"] == "Invitation"


def test_new_checks_the_email_against_the_posted_mail_type():
    """A reminder needs its Family code; a digest takes the digest rules."""
    records, _row = setup()
    data = posted(
        edit_email="on",
        subject="Reminder",
        html="<p>Please respond.</p>",
        generate_text="on",
    )
    data["schedule-kind"] = "reminder"
    _toggle, form, _sources = schedule_email.editor(data, records, None, CAMPAIGN)
    assert form.slot == "reminder"
    assert not form.is_valid()
    assert "{{ family_code }}" in " ".join(form.non_field_errors())
    data["html"] = "<p>Please respond." + CODE + "</p>"
    _toggle, form, _sources = schedule_email.editor(data, records, None, CAMPAIGN)
    assert form.is_valid(), form.errors
    added = schedule_email.copy(
        form, records, records[0]["id"], campaign_id=CAMPAIGN, kind="reminder"
    )
    assert added["values"]["slot"] == "reminder"
    # The same reminder text posted as a digest takes the digest rules,
    # which refuse a Family's code and link.
    data["schedule-kind"] = "daily_digest"
    _toggle, form, _sources = schedule_email.editor(data, records, None, CAMPAIGN)
    assert form.slot == "daily_digest"
    assert not form.is_valid()
    assert "documented placeholders" in " ".join(form.non_field_errors())
    # An unknown mail type is never a content slot.
    data["schedule-kind"] = "page"
    _toggle, form, _sources = schedule_email.editor(data, records, None, CAMPAIGN)
    assert form.slot is None


def test_new_states_the_family_access_rule_and_gates_on_the_mail_type():
    """New's help names the code and link rule before a type is chosen.

    Its checkbox and Reset wait for a mail type, with a reserved reason line;
    Edit, whose type is fixed, has no such line.
    """
    records, row = setup()
    toggle, form, sources = schedule_email.editor(None, records, None, CAMPAIGN)
    context = {"email_toggle": toggle, "email": form, "email_sources": sources}
    page = render_to_string("stewardship/schedule-email.html", context | {"new": True})
    # The long help is in the plain text's "i" toggletip.
    assert "Initial invitation or a Reminder, both the formatted" in page
    assert "data-schedule-email-reason data-idle" in page
    assert "Choose a mail type first." in page
    assert "checked against the chosen type" in page
    toggle, form, sources = schedule_email.editor(None, records, row, CAMPAIGN)
    page = render_to_string(
        "stewardship/schedule-email.html",
        {"email_toggle": toggle, "email": form, "email_sources": sources},
    )
    assert "data-schedule-email-reason" not in page


def test_the_editor_renders_hidden_until_ticked_with_its_texts():
    """The fields follow the checkbox; the texts are JSON, not markup."""
    records, row = setup()
    records[1]["values"]["subject"] = "</script><b>Second</b>"
    toggle, form, sources = schedule_email.editor(None, records, row, CAMPAIGN)
    page = render_to_string(
        "stewardship/schedule-email.html",
        {"email_toggle": toggle, "email": form, "email_sources": sources},
    )
    assert "Edit this schedule&#x27;s email" in page
    assert 'data-show-when="edit_email=on"' in page
    # The change gate counts the email's fields, never the checkbox itself.
    assert 'name="edit_email" data-change-ignore' in page
    assert "Reset to the default text" in page
    assert "data-plain-text" in page and 'name="generate_text"' in page
    # No visual editor and no banner choice here.
    assert "data-visual-content" not in page and "show_banner" not in page
    assert "</script><b>" not in page
    start = page.index('id="schedule-email-sources"')
    payload = page[page.index(">", start) + 1 : page.index("</script>", start)]
    assert json.loads(payload)["emails"][records[1]["id"]]["subject"] == (
        "</script><b>Second</b>"
    )


def test_code_and_link_placeholders_stay_required_in_both_parts():
    """Hand-written plain text without the link is refused too."""
    records, row = setup()
    data = posted(
        edit_email="on",
        subject="Reminder",
        html="<p>Please respond." + CODE + "</p>",
        text="Please respond. {{ family_code }}",
    )
    _toggle, form, _sources = schedule_email.editor(data, records, row, CAMPAIGN)
    assert not form.is_valid()
    assert "{{ family_url }}" in " ".join(form.non_field_errors())


def test_a_whole_email_refusal_is_put_at_its_html_source():
    """``refuse`` marks a field, so the page can place and link the message."""
    records, row = setup()
    data = QueryDict(mutable=True)
    data.update(
        {
            "edit_email": "on",
            "subject": "Changed",
            "html": "<p>Changed." + CODE + "</p>",
            "generate_text": "on",
        }
    )
    _, form, _ = schedule_email.editor(data, records, row, CAMPAIGN)
    assert form.is_valid(), form.errors
    schedule_email.refuse(form, "Refused.")
    assert form.errors["html"] == ["Refused."]
    assert not form.non_field_errors()
    assert {"message": "Refused.", "field_id": "id_html"} in _summary(None, None, form)
