"""New and Edit scheduled email's Basic and Advanced modes (#1129).

Through a real Admin session: a page drawn for a GET opens in Basic, with
Repeat and the schedule's own email in hidden Advanced panels; the mode is
posted with Review changes but never saved; a refused Review sent from
Advanced, or with a tool ticked, is drawn again in Advanced; an unknown mode
is refused and records nothing.
"""

from html import unescape

import pytest

from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest

from .auth_builders import signed_in
from .test_campaign_views_postgresql import post
from .test_schedule_entry_postgresql import NEW, definition, entry, setup

pytestmark = pytest.mark.django_db(transaction=True)

BASIC_CHECKED = 'name="editor_mode" value="basic" data-editor-mode-choice checked'
ADVANCED_CHECKED = 'name="editor_mode" value="advanced" data-editor-mode-choice checked'
HIDDEN_PANEL = 'data-editor-mode-panel="advanced" hidden'


def page_text(response):
    """A response's HTML, with entities decoded."""
    return unescape(response.content.decode())


def test_new_and_edit_open_in_basic_with_the_tools_hidden(auth_service, google):
    """Both pages draw the toggle in Basic; New hides Repeat and the email
    tool, Edit the email tool (it has no Repeat)."""
    store = auth_service.store
    setup(store)
    browser, _ = signed_in()
    new = page_text(browser.get(NEW))
    assert 'data-editor-modes="schedule-entry"' in new
    assert BASIC_CHECKED in new and ADVANCED_CHECKED not in new
    assert new.count(HIDDEN_PANEL) == 2
    assert "Your custom schedule has no basic equivalent." in new
    edit = page_text(
        browser.get(f"/admin/campaign/schedules/{definition('reminder').pk}/")
    )
    assert BASIC_CHECKED in edit
    assert edit.count(HIDDEN_PANEL) == 1


def test_a_refused_review_keeps_the_mode_it_was_sent_from(auth_service, google):
    """A Review refused for its values is drawn in the mode it was sent from,
    and in Advanced whenever Repeat was ticked; nothing is recorded."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    before = ConfigurationChangeRequest.objects.count()
    # November 15 is after the campaign ends.
    outside = entry(
        store,
        kind="reminder",
        date="2054-11-15",
        time="9am",
        template_version=emails["reminder"]["id"],
    )
    basic = post(browser, NEW, outside | {"editor_mode": "basic"})
    assert basic.status_code == 400
    assert BASIC_CHECKED in page_text(basic)
    advanced = post(browser, NEW, outside | {"editor_mode": "advanced"})
    assert advanced.status_code == 400
    assert ADVANCED_CHECKED in page_text(advanced)
    assert HIDDEN_PANEL not in page_text(advanced)
    # Repeat ticked with no dates that fit: refused, and drawn in Advanced.
    repeat = post(
        browser, NEW, outside | {"editor_mode": "basic", "repeat-repeat": "on"}
    )
    assert repeat.status_code == 400
    assert ADVANCED_CHECKED in page_text(repeat)
    assert ConfigurationChangeRequest.objects.count() == before


def test_the_mode_is_posted_but_never_saved_and_an_unknown_one_is_refused(
    auth_service, google
):
    """Review changes from Advanced reviews as from Basic; an unknown mode is
    the page's unexpected-field refusal, recording nothing."""
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
    for mode in ("basic", "advanced"):
        review = post(browser, NEW, fields | {"editor_mode": mode})
        assert review.status_code == 200, review.content
        assert "data-review-answer" in review.content.decode()
    refused = post(browser, NEW, fields | {"editor_mode": "simple"})
    assert refused.status_code == 400
    assert "this page sent something the server did not expect" in page_text(refused)
    assert ConfigurationChangeRequest.objects.count() == before


def test_a_repeat_left_ticked_under_another_mail_type_is_not_a_tool_in_use(
    auth_service, google
):
    """Repeat is a Reminder's: ticked with another mail type it is hidden, so
    the refused page is drawn in the mode it was sent from (Basic here)."""
    store = auth_service.store
    _, emails = setup(store)
    browser, _ = signed_in()
    fields = entry(
        store,
        kind="initial",
        date="2054-11-15",
        time="9am",
        template_version=emails["initial"]["id"],
    )
    refused = post(
        browser, NEW, fields | {"editor_mode": "basic", "repeat-repeat": "on"}
    )
    assert refused.status_code == 400
    assert ADVANCED_CHECKED not in page_text(refused)
