"""New and Edit scheduled email: Sends (Once or Repeating), no modes (#2022).

Through a real Admin session, following the Administrator's R43 and R44
decisions on the date-change review (#1944): New draws only Mail type until
one is chosen, with Sends and the email tool hidden; a Reminder's Sends is
posted as ``repeat-sends`` while it is shown; a refused Review is drawn as
posted, so nothing flashes; Edit has no Sends. The Basic and Advanced
toggle is gone, so a posted ``editor_mode`` or old Repeat checkbox is
refused and records nothing.
"""

import re
from html import unescape

import pytest

from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest

from .auth_builders import signed_in
from .test_campaign_views_postgresql import post
from .test_schedule_entry_postgresql import NEW, definition, entry, setup

pytestmark = pytest.mark.django_db(transaction=True)

# Sends' radios as drawn: disabled until the page script shows them.
SENDS = re.compile(
    r'<input type="radio" id="repeat-sends_(\d)" name="repeat-sends"[^>]*>'
)
EMAIL_HIDDEN = '<div class="schedule-email" data-schedule-email hidden>'
EMAIL_SHOWN = '<div class="schedule-email" data-schedule-email>'
PANEL_HIDDEN = re.compile(r'data-repeat-entry [^>]*data-repeat-limit="\d+" hidden>')
RULE_HIDDEN = "<div data-repeat-rule hidden>"


def page_text(response):
    """A response's HTML, with entities decoded."""
    return unescape(response.content.decode())


def outside(store, emails, kind="reminder"):
    """A New post for November 15, after the campaign ends: refused."""
    return entry(
        store,
        kind=kind,
        date="2054-11-15",
        time="9am",
        template_version=emails[kind]["id"],
    )


def test_new_shows_only_mail_type_and_edit_has_no_sends(auth_service, google):
    """New draws no toggle, Sends (Once to start) and the rule hidden and
    disabled, and the email tool hidden; Edit of a reminder shows the email
    tool and no Sends. (A report's Edit: test_schedule_entry_sends.py.)"""
    store = auth_service.store
    setup(store)
    browser, _ = signed_in()
    new = page_text(browser.get(NEW))
    for gone in ('name="editor_mode"', "data-editor-modes", 'name="repeat-repeat"'):
        assert gone not in new
    assert "Choose a mail type first." not in new
    radios = SENDS.findall(new)
    assert radios == ["0", "1"]
    tags = [match.group(0) for match in SENDS.finditer(new)]
    assert all(" disabled" in tag for tag in tags)
    assert 'value="once" checked' in tags[0]
    assert PANEL_HIDDEN.search(new) and RULE_HIDDEN in new and EMAIL_HIDDEN in new
    # Sends is drawn right after Mail type, before the send date.
    kind = new.index('name="schedule-kind"')
    assert kind < new.index('id="repeat-sends"') < new.index('name="schedule-date"')
    edit = page_text(
        browser.get(f"/admin/campaign/schedules/{definition('reminder').pk}/")
    )
    assert 'name="editor_mode"' not in edit and "repeat-sends" not in edit
    assert EMAIL_SHOWN in edit


def test_a_refused_review_is_drawn_as_posted(auth_service, google):
    """A refused Once draws Sends shown with Once and the email tool shown;
    a refused Repeating draws Repeating and the rule shown too; an
    invitation keeps Sends hidden. Nothing is recorded."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    before = ConfigurationChangeRequest.objects.count()
    once = post(browser, NEW, outside(store, emails) | {"repeat-sends": "once"})
    assert once.status_code == 400
    page = page_text(once)
    assert not PANEL_HIDDEN.search(page) and RULE_HIDDEN in page
    assert EMAIL_SHOWN in page
    assert re.search(r'value="once" checked', page)
    # Repeating with a valid rule whose dates are all after the campaign,
    # so the page posts none.
    repeating = post(
        browser,
        NEW,
        outside(store, emails)
        | {
            "repeat-sends": "repeat",
            "repeat-frequency": "daily",
            "repeat-start": "2054-11-10",
            "repeat-until": "2054-11-12",
        },
    )
    assert repeating.status_code == 400
    page = page_text(repeating)
    assert not PANEL_HIDDEN.search(page) and RULE_HIDDEN not in page
    assert re.search(r'value="repeat" checked', page)
    assert "or choose Once to send on one date." in page
    # The summary links the refusal at Sends to its first choice.
    assert 'href="#repeat-sends_0"' in page
    invitation = post(browser, NEW, outside(store, emails, "initial"))
    assert invitation.status_code == 400
    page = page_text(invitation)
    assert PANEL_HIDDEN.search(page) and EMAIL_SHOWN in page
    assert ConfigurationChangeRequest.objects.count() == before


def test_sends_is_checked_and_old_fields_are_refused(auth_service, google):
    """Once (or no Sends) reviews one schedule; Repeating with an invitation
    is refused at Sends; an unknown Sends value, a posted mode and the old
    Repeat checkbox are refused, recording nothing."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    fields = entry(
        store,
        kind="reminder",
        date="2054-10-15",
        time="9am",
        template_version=emails["reminder"]["id"],
    )
    before = ConfigurationChangeRequest.objects.count()
    for sends in ({}, {"repeat-sends": "once"}):
        review = post(browser, NEW, fields | sends)
        assert review.status_code == 200, review.content
        assert "data-review-answer" in review.content.decode()
    invitation = entry(
        store,
        kind="initial",
        date="2054-10-02",
        time="9am",
        template_version=emails["initial"]["id"],
    )
    refused = post(
        browser,
        NEW,
        invitation | {"repeat-sends": "repeat", "repeat-dates": "2054-10-13"},
    )
    assert refused.status_code == 400
    assert "Only reminders repeat." in page_text(refused)
    odd = post(browser, NEW, fields | {"repeat-sends": "on"})
    assert odd.status_code == 400
    assert re.search(r'id="repeat-sends_error"(?! hidden)', page_text(odd))
    for old in ({"editor_mode": "advanced"}, {"repeat-repeat": "on"}):
        stale = post(browser, NEW, fields | old)
        assert stale.status_code == 400
        assert "this page sent something the server did not expect" in page_text(stale)
    edit = f"/admin/campaign/schedules/{definition('reminder').pk}/"
    edited = entry(
        store,
        date="2054-10-25",
        time="10:30",
        template_version=emails["reminder"]["id"],
    )
    assert post(browser, edit, edited | {"editor_mode": "basic"}).status_code == 400
    assert ConfigurationChangeRequest.objects.count() == before
