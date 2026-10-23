"""The date-change review's New schedule pane, through the server (#1944).

Through a real Admin session: the review draws one New schedule pane, the
formset's first new row, with only its Mail type to start and its Sends pair
drawn disabled, after the schedules table, inside the page's in-place region.
Review covers the pane's one schedule: Once posts the pane row itself, and
Repeating posts one ordinary reminder row per date in its place (as the page
script numbers them); Apply saves it with any proposed dates, and the page's
own address then draws the new schedule, an empty pane and, once the dates
are saved, no proposed-dates notice. The server's checks are unchanged, and a
posted Sends is refused as an unexpected field, recording nothing.
"""

import re
from html import unescape
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.models import Campaign

from ..content_factory import content
from .auth_builders import signed_in
from .campaign_builders import change
from .test_campaign_views_postgresql import apply, post
from .test_parish_views_postgresql import token
from .test_schedule_views_postgresql import fields, setup

pytestmark = pytest.mark.django_db(transaction=True)

NOTICE = "The proposed dates below have not been saved."


def page_text(response):
    """A response's HTML, with entities decoded."""
    return unescape(response.content.decode())


def reminder_email(store, campaign):
    """Save a reminder email the pane can send, and return its ID."""
    email = content(
        str(campaign.pk), kind="email", slot="reminder", subject="Pane mail"
    )
    saved = change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "content", **email}],
    )
    assert saved.state == "applied"
    return email["id"]


def with_pane(data, email, days, *, time="09:00"):
    """``data`` with the pane's one schedule: a reminder on each of ``days``.

    One day is a Once pane, posted as the pane row itself (row
    INITIAL_FORMS). Several are a Repeating pane, which the page script
    posts as one reminder row per date in the pane's place, numbered on
    from the pane's index.
    """
    pane = int(data["schedules-INITIAL_FORMS"])
    data = data | {"schedules-TOTAL_FORMS": str(pane + len(days))}
    for offset, day in enumerate(days, start=pane):
        data |= {
            f"schedules-{offset}-kind": "reminder",
            f"schedules-{offset}-date": day,
            f"schedules-{offset}-time": time,
            f"schedules-{offset}-template_version": email,
        }
    return data


def reminder_days(store, campaign):
    """The saved reminders' send dates, in order."""
    document = store.active().document()
    return sorted(
        row["values"]["date"]
        for row in document["sections"]["schedules"]
        if row["values"]["campaign_id"] == str(campaign.pk)
        and row["values"]["kind"] == "reminder"
    )


def review_and_apply(store, browser, path, data):
    """Review ``data`` (accepted, nothing recorded) and Apply it; the review."""
    before = ConfigurationChangeRequest.objects.count()
    review = post(browser, path, data)
    assert review.status_code == 200, re.findall(r"<li>([^<]+)</li>", page_text(review))
    assert ConfigurationChangeRequest.objects.count() == before
    apply(store, post(browser, path, {"action": "confirm", "preview": token(review)}))
    return page_text(review)


def test_the_review_draws_one_pane_with_only_its_mail_type(auth_service, google):
    """The pane follows the table inside the in-place region; its Sends is
    disabled and its rule unnamed. No Add this schedule, Added schedules,
    Add another schedule, Mode toggle or Repeat a reminder is drawn, and
    the review dialog redraws the page rather than returning to the list."""
    store = auth_service.store
    _, path = setup(store)
    browser, _ = signed_in()
    page = page_text(browser.get(f"{path}?end_date=2054-10-28"))
    assert len(re.findall(r"data-schedule-pane[ >]", page)) == 1
    assert page.index("data-schedule-table") < page.index("data-schedule-pane")
    region = page.index('<div id="schedule-dates" data-in-place-region>')
    assert region < page.index(NOTICE) < page.index('id="schedule-dates-form"')
    assert 'id="schedule-dates-outcome"' in page
    assert 'data-review-return=""' in page
    assert 'data-review-outcome="schedule-dates-outcome"' in page
    text = page[page.index('class="panel schedule-pane"') :]
    assert text.index("<legend>New schedule</legend>") < 400
    assert "data-schedule-sends hidden" in text
    assert "data-schedule-pane-line hidden" in text

    sends = re.findall(r'<input[^>]*name="schedule_sends"[^>]*>', page)
    assert len(sends) == 2 and all(" disabled" in tag for tag in sends)
    rule = text[
        text.index("data-repeat-panel") : text.index('data-schedule-field="date"')
    ]
    assert re.findall(r"<(?:input|select)[^>]*\bname=", rule) == []
    for gone in (
        "Add this schedule",
        "Added schedules",
        "data-schedule-template",
        "Add another schedule",
        "Repeat a reminder",
        'id="schedule-dates-mode"',
    ):
        assert gone not in page


def test_a_once_schedule_applies_with_the_proposed_dates(auth_service, google):
    """A Once reminder after a later proposed end is reviewed with the dates
    and applied with them; the page's own address then draws it in the
    table, an empty pane and no proposed-dates notice."""
    store = auth_service.store
    campaign, path = setup(store)
    browser, _ = signed_in()
    email = reminder_email(store, campaign)
    address = f"{path}?end_date=2054-11-02"
    assert NOTICE in page_text(browser.get(address))
    data, _ = fields(store, campaign)
    data["window-end_date"] = "2054-11-02"
    review = review_and_apply(
        store, browser, path, with_pane(data, email, ["2054-11-02"])
    )
    # A short confirmation (R45): what is added and the end date's change.
    assert "Add a reminder:" in review and "November 2, 2054" in review
    assert "Campaign end date: October 31, 2054 → November 2, 2054." in review
    assert "Current schedule" not in review and "Campaign window" not in review
    campaign.refresh_from_db()
    assert campaign.active_configuration.values["end_date"] == "2054-11-02"
    assert reminder_days(store, campaign) == ["2054-10-25", "2054-11-02"]
    fresh = page_text(browser.get(address))
    assert NOTICE not in fresh
    pane = int(fields(store, campaign)[0]["schedules-INITIAL_FORMS"])
    assert pane == 3
    assert re.search(
        rf'<select name="schedules-{pane}-kind"[^>]*>\s*<option value="" selected>',
        fresh,
    )
    assert fresh.count("data-schedule-choose") == 3


def test_a_once_schedule_applies_without_a_date_change(auth_service, google):
    """With the proposed dates put back, Review covers only the pane's one
    schedule, which Apply adds; the notice stays, the dates still being a
    proposal on this address."""
    store = auth_service.store
    campaign, path = setup(store)
    browser, _ = signed_in()
    email = reminder_email(store, campaign)
    data, _ = fields(store, campaign)
    review = review_and_apply(
        store, browser, path, with_pane(data, email, ["2054-10-27"])
    )
    assert "Campaign end date" not in review and "October 27, 2054" in review
    assert "Add a reminder:" in review
    campaign.refresh_from_db()
    assert campaign.active_configuration.values["end_date"] == "2054-10-31"
    assert reminder_days(store, campaign) == ["2054-10-25", "2054-10-27"]
    assert NOTICE in page_text(browser.get(f"{path}?end_date=2054-11-02"))


def test_a_repeating_schedule_applies_as_its_reminders(auth_service, google):
    """A Repeating pane posts one reminder per date; the review lists each
    date and Apply adds them all, with or without a date change."""
    store = auth_service.store
    campaign, path = setup(store)
    browser, _ = signed_in()
    email = reminder_email(store, campaign)
    data, _ = fields(store, campaign)
    review = review_and_apply(
        store, browser, path, with_pane(data, email, ["2054-10-26", "2054-10-28"])
    )
    assert "October 26, 2054" in review and "October 28, 2054" in review
    assert "Add 2 reminders:" in review and "Current schedule" not in review
    assert reminder_days(store, campaign) == ["2054-10-25", "2054-10-26", "2054-10-28"]
    # A second Repeating schedule, with a later proposed end this time.
    data, _ = fields(store, campaign)
    data["window-end_date"] = "2054-11-04"
    days = ["2054-11-01", "2054-11-02", "2054-11-03"]
    review = review_and_apply(store, browser, path, with_pane(data, email, days))
    assert all(f"November {day}, 2054" in review for day in (1, 2, 3)), (
        "each date is listed"
    )
    assert "Add 3 reminders:" in review and "Campaign end date:" in review
    campaign = Campaign.objects.get()
    assert campaign.active_configuration.values["end_date"] == "2054-11-04"
    assert reminder_days(store, campaign)[-3:] == days


@pytest.mark.parametrize(
    ("days", "time", "end", "words"),
    [
        # Outside the proposed dates (the saved end is October 31).
        (["2054-11-02"], "09:00", "2054-10-28", "campaign"),
        # The same date and time as the saved reminder.
        (["2054-10-25"], None, None, "same"),
        # At or before the Initial invitation.
        (["2054-10-01"], "00:00", None, "nitial invitation"),
        # One of a Repeating schedule's reminders outside the dates.
        (["2054-10-27", "2054-11-15"], "09:00", "2054-10-28", "campaign"),
    ],
)
def test_the_server_refuses_the_pane_as_before(
    auth_service, google, days, time, end, words
):
    """The server's checks are unchanged: a refused Review is the page again
    (400) with its message, and nothing is recorded."""
    store = auth_service.store
    campaign, path = setup(store)
    browser, _ = signed_in()
    email = reminder_email(store, campaign)
    data, indexes = fields(store, campaign)
    if end:
        data["window-end_date"] = end
    if time is None:
        time = data[f"schedules-{indexes['reminder']}-time"]
    before = ConfigurationChangeRequest.objects.count()
    refused = post(browser, path, with_pane(data, email, days, time=time))
    assert refused.status_code == 400
    assert words in page_text(refused)
    assert ConfigurationChangeRequest.objects.count() == before
    assert reminder_days(store, campaign) == ["2054-10-25"]


def test_a_posted_sends_is_refused_and_records_nothing(auth_service, google):
    """Sends is never sent by the page (disabled while hidden, and dropped
    from the form's data), so a post carrying it is the page's
    unexpected-field refusal."""
    store = auth_service.store
    campaign, path = setup(store)
    browser, _ = signed_in()
    email = reminder_email(store, campaign)
    data, _ = fields(store, campaign)
    data = with_pane(data, email, ["2054-10-26"])
    before = ConfigurationChangeRequest.objects.count()
    refused = post(browser, path, data | {"schedule_sends": "repeat"})
    assert refused.status_code == 400
    assert "this page sent something the server did not expect" in page_text(refused)
    assert ConfigurationChangeRequest.objects.count() == before
