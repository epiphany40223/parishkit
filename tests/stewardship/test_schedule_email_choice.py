"""A schedule's email list tells look-alike emails apart and describes each (#446)."""

import json
from html import unescape

from parishkit.stewardship.accounts.schedule_forms import (
    Schedules,
    email_choices,
    email_usage,
    excerpt,
    schedule_labels,
    schedule_order,
)

from .campaign_factory import campaign, schedule
from .content_factory import content

OWNER = campaign()


def emails():
    """Two reminder emails with the same subject and one initial invitation."""
    owner = OWNER["id"]
    return [
        content(owner, kind="email", slot="initial", subject="Invitation"),
        content(owner, kind="email", slot="reminder", subject="Please respond"),
        content(
            owner,
            kind="email",
            slot="reminder",
            subject="Please respond",
            text="A   second\nreminder  for {{ parish_name }}.",
        ),
    ]


def saved(rows):
    """An invitation and two reminders, each sending a saved email."""
    owner = OWNER["id"]
    initial, first, second = rows
    return [
        schedule(
            owner,
            kind="reminder",
            date="2054-10-20",
            template_version=first["id"],
            subject="Please respond",
        ),
        schedule(owner, template_version=initial["id"], subject="Invitation"),
        schedule(
            owner,
            kind="reminder",
            date="2054-10-10",
            template_version=first["id"],
            subject="Please respond",
        ),
    ]


def formset(templates, previous, **options):
    """The page's unbound formset."""
    return Schedules(
        prefix="schedules",
        previous=previous,
        templates=templates,
        campaign_id=OWNER["id"],
        campaign=OWNER["values"],
        **options,
    )


def test_schedule_labels_number_reminders_in_the_given_order():
    """The same names as the schedules table and Family email history."""
    rows = formset(emails(), saved(emails())).previous
    assert schedule_labels(rows) == [
        "Initial invitation",
        "Reminder 1",
        "Reminder 2",
    ]


def test_choices_say_who_sends_each_email_and_never_read_the_same():
    """Usage tells emails apart; an ID is added only to break a tie."""
    rows = emails()
    initial, first, second = rows
    labels = dict(email_choices(rows, {first["id"]: ["Reminder 1", "Reminder 2"]}))
    assert labels[first["id"]] == "Please respond — sent by Reminder 1, Reminder 2"
    assert labels[second["id"]] == "Please respond — not sent by any schedule"
    assert labels[initial["id"]] == "Invitation — not sent by any schedule"
    tie = dict(email_choices(rows, {}))
    assert tie[first["id"]] == (
        f"Please respond — not sent by any schedule ({first['id'][:8]})"
    )
    assert tie[second["id"]].endswith(f"({second['id'][:8]})")
    assert tie[initial["id"]] == "Invitation — not sent by any schedule"


def test_a_saved_reminder_offers_its_type_with_usage_labels():
    """Only reminder emails, labelled by the schedules that send them."""
    rows = emails()
    initial, first, second = rows
    schedules = formset(rows, saved(rows))
    reminder = schedules.forms[1]
    assert reminder.fields["template_version"].choices == [
        ("", "Choose a template"),
        (first["id"], "Please respond — sent by Reminder 1, Reminder 2"),
        (second["id"], "Please respond — not sent by any schedule"),
    ]


def test_options_carry_the_summary_and_links_only_when_asked():
    """The page script reads each option's excerpt, users and addresses."""
    rows = emails()
    initial, first, second = rows
    plain = str(formset(rows, saved(rows)).forms[1]["template_version"])
    assert "data-used-by" in plain and "data-edit-url" not in plain
    linked = unescape(
        str(formset(rows, saved(rows), email_links=True).forms[1]["template_version"])
    )
    assert f'data-test-url="/admin/campaign/content/test/{first["id"]}/"' in linked
    assert (
        f'data-edit-url="/admin/campaign/content/email/reminder/{first["id"]}/"'
        in linked
    )
    users = json.dumps(["Reminder 1", "Reminder 2"])
    assert f'data-used-by="{users}"' in linked
    assert 'data-excerpt="A second reminder for {{ parish_name }}."' in linked
    # The blank new row gets the same details, so a new schedule's choice is
    # described as well.
    blank = str(formset(rows, saved(rows), email_links=True).empty_form)
    assert "data-edit-url" in blank


def test_excerpt_is_one_line_cut_at_a_word():
    """Long text is shortened without splitting a word."""
    assert excerpt("one\n\ntwo   three") == "one two three"
    assert excerpt("alpha beta gamma", limit=12) == "alpha beta …"
    assert excerpt(None) == ""
    # A cut never ends inside a placeholder.
    assert excerpt("Dear {{ family_name }} friend", limit=12) == "Dear …"
    assert excerpt("Dear {{ family_name }} friend", limit=24) == (
        "Dear {{ family_name }} …"
    )
    # A placeholder at the very start leaves only the ellipsis.
    assert excerpt("{{ family_name }} " + "word " * 30, limit=12) == "…"


def test_email_usage_names_senders_in_sending_order():
    """Each email maps to the schedules that send it, named as on the table."""
    rows = emails()
    usage = email_usage(sorted(saved(rows), key=schedule_order))
    initial, first, second = rows
    assert usage[initial["id"]] == ["Initial invitation"]
    assert usage[first["id"]] == ["Reminder 1", "Reminder 2"]
    assert second["id"] not in usage


def test_every_saved_form_knows_its_schedule_name():
    """Setup and copy pages build the formset without the table (#446)."""
    rows = emails()
    schedules = formset(rows, saved(rows))
    assert [form.schedule_label for form in schedules.forms] == [
        "Initial invitation",
        "Reminder 1",
        "Reminder 2",
        None,
    ]
    assert schedules.empty_form.schedule_label is None
