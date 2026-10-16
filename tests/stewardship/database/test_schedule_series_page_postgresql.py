"""Reminder series on New and Edit scheduled email (#469 slice 2b-3).

Through real Admin sessions and the configuration installer: New's Repeat
remembers its rule as a series in a users-form configuration; Edit of a
series' reminder says "Part of" its rule and changes "this and following"
or "the whole series" with the command line's plan, through the same
review, signed preview and confirmation. Refusals change nothing.
"""

import json
from datetime import UTC, datetime
from html import unescape
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import schedule_entry_views
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.series_edits import rule_text
from parishkit.stewardship.campaigns.recurrence import canonical_rule

from ..content_factory import content
from .auth_builders import signed_in
from .campaign_builders import change
from .test_campaign_series_postgresql import (
    FIRST,
    LAST,
    RULE,
    remembered,
    reminder,
    series_deployment,
)
from .test_campaign_views_postgresql import apply, post
from .test_parish_views_postgresql import token
from .test_schedule_entry_postgresql import LIST, NEW, entry
from .test_schedule_entry_postgresql import setup as with_saved_emails
from .user_builders import convert

pytestmark = pytest.mark.django_db(transaction=True)


def values_of(store, identifier):
    """A saved schedule's values in the active configuration."""
    return next(
        row["values"]
        for row in store.active().document()["sections"]["schedules"]
        if row["id"] == identifier
    )


def edit_post(store, identifier, zone="America/New_York", **fields):
    """An Edit page's post of a series reminder, as shown unless ``fields`` say.

    The date is left out, as the page greys it for a series scope.
    """
    saved = values_of(store, identifier)
    shown = {"time": saved["time"][:5], "template_version": saved["template_version"]}
    return entry(store, zone, **(shown | fields))


def test_a_new_repeat_is_remembered_as_a_series(auth_service, google):
    """The review names the series; applying it saves the rule and reminders."""
    store = auth_service.store
    _, emails = with_saved_emails(store)
    convert(store)
    browser, _ = signed_in()
    rule = RULE | {"start": "2054-10-10", "end": "2054-10-20"}
    data = entry(
        store, kind="reminder", time="10:00", template_version=emails["reminder"]["id"]
    ) | {
        "repeat-repeat": "on",
        "repeat-frequency": "weekly",
        "repeat-weekdays": str(RULE["weekdays"][0]),
        "repeat-start": rule["start"],
        "repeat-until": rule["end"],
        "repeat-dates": ["2054-10-11", "2054-10-18"],
    }
    review = post(browser, NEW, data)
    assert review.status_code == 200, review.content
    text = unescape(review.content.decode())
    assert f"Kept together as a series: {rule_text(canonical_rule(rule))}." in text
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    [row] = remembered(store)
    assert row["values"]["rule"] == canonical_rule(rule)
    assert row["values"]["schedules"] == sorted(
        [reminder(store, "2054-10-11"), reminder(store, "2054-10-18")]
    )


def test_a_repeat_is_not_remembered_before_the_users_form(auth_service, google):
    """A sign-in-rules configuration cannot hold a series: plain reminders."""
    store = auth_service.store
    _, emails = with_saved_emails(store)
    browser, _ = signed_in()
    data = entry(
        store, kind="reminder", time="10:00", template_version=emails["reminder"]["id"]
    ) | {
        "repeat-repeat": "on",
        "repeat-frequency": "weekly",
        "repeat-weekdays": str(RULE["weekdays"][0]),
        "repeat-start": "2054-10-10",
        "repeat-until": "2054-10-20",
        "repeat-dates": ["2054-10-11", "2054-10-18"],
    }
    review = post(browser, NEW, data)
    assert review.status_code == 200, review.content
    assert "Kept together as a series" not in review.content.decode()
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    assert "schedule_series" not in store.active().document()["sections"]


def scope_codes(response):
    """The closed codes of a refused page's series scope errors (#1437)."""
    form = response.context["series"]["form"]
    return [error.code for error in form.errors.as_data().get("scope", [])]


def test_edit_of_a_series_reminder_says_part_of_and_offers_scopes(auth_service, google):
    """Only a series' reminder gets the scope; the date greys for the others."""
    store = auth_service.store
    series_deployment(store)
    browser, _ = signed_in()
    page = unescape(
        browser.get(
            f"/admin/campaign/schedules/{reminder(store, FIRST)}/"
        ).content.decode()
    )
    assert f"Part of: {rule_text(RULE)}" in page
    for label in ("This reminder only", "This and following", "The whole series"):
        assert label in page
    assert 'name="series-scope"' in page
    assert 'data-enabled-when="series-scope=this"' in page
    assert "data-not-a-change" in page
    assert 'name="series-scope"' not in unescape(browser.get(NEW).content.decode())


def test_this_and_following_retimes_and_splits_the_series(auth_service, google):
    """From the first reminder the series is replaced in place; from the last
    it splits: the earlier reminder keeps the old rule, cut short.
    """
    store = auth_service.store
    _, identifier = series_deployment(store)
    browser, _ = signed_in()
    first = reminder(store, FIRST)
    review = post(
        browser,
        f"/admin/campaign/schedules/{first}/",
        edit_post(store, first, time="10:30") | {"series-scope": "following"},
    )
    assert review.status_code == 200, review.content
    moved = canonical_rule(RULE | {"start": FIRST})
    text = unescape(review.content.decode())
    # A time-only change names the new time; the series is not split.
    assert (
        "From October 18, 2054, every reminder of this series sends at 10:30 AM" in text
    )
    assert "stay in this series" not in text
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    [row] = remembered(store)
    assert row["id"] == identifier and row["values"]["rule"] == moved
    renewed = [reminder(store, FIRST), reminder(store, LAST)]
    assert row["values"]["schedules"] == sorted(renewed)
    assert {values_of(store, item)["time"] for item in renewed} == {"10:30:00"}

    # From the last reminder: the series keeps the first, up to the day before.
    last = reminder(store, LAST)
    review = post(
        browser,
        f"/admin/campaign/schedules/{last}/",
        edit_post(store, last, time="11:00") | {"series-scope": "following"},
    )
    assert review.status_code == 200, review.content
    text = unescape(review.content.decode())
    earlier = rule_text(canonical_rule(moved | {"end": "2054-10-24"}))
    later = rule_text(canonical_rule(moved | {"start": LAST}))
    assert f"Reminders before October 25, 2054 stay in this series: {earlier}." in text
    assert (
        f"From October 25, 2054, the changed reminders are a new series: {later}."
        in text
    )
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    kept, split = sorted(remembered(store), key=lambda row: row["id"] != identifier)
    assert kept["values"]["schedules"] == [reminder(store, FIRST)]
    assert kept["values"]["rule"]["end"] == "2054-10-24"
    assert split["values"]["schedules"] == [reminder(store, LAST)]
    assert values_of(store, reminder(store, FIRST))["time"] == "10:30:00"
    assert values_of(store, reminder(store, LAST))["time"] == "11:00:00"


def test_series_scope_refusals_change_nothing(auth_service, google):
    """No change, a day moved by the time, and a stale scope are refused."""
    store = auth_service.store
    series_deployment(store)
    browser, _ = signed_in()
    first = reminder(store, FIRST)
    path = f"/admin/campaign/schedules/{first}/"
    before = ConfigurationChangeRequest.objects.count()
    # Choosing a scope alone changes nothing: the dialog's plain refusal.
    same = browser.post(
        path,
        edit_post(store, first)
        | {
            "series-scope": "series",
            "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert same.status_code == 400
    assert json.loads(same.content)["refusal"]["message"].startswith(
        "Nothing has changed."
    )
    # 11:00 PM on Kiritimati (UTC+14) is the next morning's in New York.
    moved = post(
        browser,
        path,
        edit_post(store, first, zone="Pacific/Kiritimati", time="23:00")
        | {"series-scope": "following"},
    )
    assert moved.status_code == 400
    page = unescape(moved.content.decode())
    assert "would send on another day" in page
    assert 'href="#id_series-scope_0"' in page
    assert scope_codes(moved) == ["series_edit_refused"]
    assert ConfigurationChangeRequest.objects.count() == before


def test_a_scope_for_a_reminder_outside_a_series_is_refused(auth_service, google):
    """A page drawn while the reminder was in a series says so; nothing changes."""
    store = auth_service.store
    _, emails = with_saved_emails(store)
    convert(store)
    browser, _ = signed_in()
    alone = reminder(store, LAST)
    before = ConfigurationChangeRequest.objects.count()
    refused = browser.post(
        f"/admin/campaign/schedules/{alone}/",
        edit_post(store, alone, time="10:30")
        | {
            "series-scope": "following",
            "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert refused.status_code == 400
    assert json.loads(refused.content)["refusal"]["message"] == (
        "This reminder is no longer part of a series."
    )
    assert ConfigurationChangeRequest.objects.count() == before


def test_the_whole_series_takes_a_new_email(auth_service, google):
    """Every unsent reminder sends the email chosen, at its own time."""
    store = auth_service.store
    campaign, _ = series_deployment(store)
    other = content(
        str(campaign.pk), kind="email", slot="reminder", subject="Another reminder"
    )
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "content", **other}],
        ).state
        == "applied"
    )
    browser, _ = signed_in()
    last = reminder(store, LAST)
    review = post(
        browser,
        f"/admin/campaign/schedules/{last}/",
        edit_post(store, last, template_version=other["id"])
        | {"series-scope": "series"},
    )
    assert review.status_code == 200, review.content
    assert (
        "From October 18, 2054, every reminder of this series sends the chosen "
        "email." in unescape(review.content.decode())
    )
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    renewed = [reminder(store, FIRST), reminder(store, LAST)]
    assert {values_of(store, item)["template_version"] for item in renewed} == {
        other["id"]
    }
    assert {values_of(store, item)["time"] for item in renewed} == {"09:00:00"}
    [row] = remembered(store)
    assert row["values"]["schedules"] == sorted(renewed)


def read_only(monkeypatch, *identifiers):
    """Make the page read ``identifiers`` as sent or being prepared."""
    monkeypatch.setattr(
        schedule_entry_views, "_read_only", lambda saved, campaign: set(identifiers)
    )


def test_scopes_a_sent_reminder_blocks_are_unavailable_with_why(
    auth_service, google, monkeypatch
):
    """Both scopes from the first reminder are drawn unavailable, in the
    page's words; a post naming one anyway is refused at the choice."""
    store = auth_service.store
    series_deployment(store)
    read_only(monkeypatch, reminder(store, LAST))
    browser, _ = signed_in()
    first = reminder(store, FIRST)
    path = f"/admin/campaign/schedules/{first}/"
    page = unescape(browser.get(path).content.decode())
    assert page.count('type="radio" name="series-scope"') == 3
    assert page.count(' disabled aria-describedby="id_series-scope_') == 2
    assert (
        "Unavailable: A reminder of this series from this one on has already "
        "sent or is being prepared." in page
    )
    assert (
        "Unavailable: A reminder of this series after its first unsent one has "
        "already sent or is being prepared" in page
    )
    # The page's own words, never the command line's change-document terms.
    assert '("following")' not in page
    before = ConfigurationChangeRequest.objects.count()
    refused = post(
        browser,
        path,
        edit_post(store, first, time="10:30") | {"series-scope": "following"},
    )
    assert refused.status_code == 400
    assert scope_codes(refused) == ["series_edit_refused"]
    assert ConfigurationChangeRequest.objects.count() == before


def test_a_sent_reminder_stays_as_it_is_through_the_whole_series(
    auth_service, google, monkeypatch
):
    """The series splits before it: same id, time and email; later ones change."""
    store = auth_service.store
    _, identifier = series_deployment(store)
    first = reminder(store, FIRST)
    saved = values_of(store, first)
    read_only(monkeypatch, first)
    browser, _ = signed_in()
    last = reminder(store, LAST)
    review = post(
        browser,
        f"/admin/campaign/schedules/{last}/",
        edit_post(store, last, time="11:00") | {"series-scope": "series"},
    )
    assert review.status_code == 200, review.content
    assert "Reminders before October 25, 2054 stay in this series" in unescape(
        review.content.decode()
    )
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    assert reminder(store, FIRST) == first
    assert values_of(store, first) == saved
    assert values_of(store, reminder(store, LAST))["time"] == "11:00:00"
    kept = next(row for row in remembered(store) if row["id"] == identifier)
    assert kept["values"]["schedules"] == [first]


def test_a_missed_reminder_makes_a_series_change_unavailable(
    auth_service, google, monkeypatch
):
    """Review A's case: on October 20 the October 18 reminder has passed
    without sending; the whole series from the 25th would add it again on
    its past date and send it at once, so the page refuses, naming the date.
    This and following from the 25th leaves it alone and is allowed."""
    store = auth_service.store
    series_deployment(store)
    pinned = datetime(2054, 10, 20, 12, tzinfo=UTC)
    monkeypatch.setattr(
        schedule_entry_views, "timezone", SimpleNamespace(now=lambda: pinned)
    )
    browser, _ = signed_in()
    last = reminder(store, LAST)
    path = f"/admin/campaign/schedules/{last}/"
    page = unescape(browser.get(path).content.decode())
    missed = (
        "The reminder of this series on October 18, 2054 has passed without "
        "sending, so changing it with the others would send it at once."
    )
    assert f"Unavailable: {missed}" in page
    before = ConfigurationChangeRequest.objects.count()
    refused = post(
        browser, path, edit_post(store, last, time="10:30") | {"series-scope": "series"}
    )
    assert refused.status_code == 400
    assert missed in unescape(refused.content.decode())
    assert scope_codes(refused) == ["series_edit_refused"]
    assert ConfigurationChangeRequest.objects.count() == before
    allowed = post(
        browser,
        path,
        edit_post(store, last, time="10:30") | {"series-scope": "following"},
    )
    assert allowed.status_code == 200, allowed.content


def pin(monkeypatch, moment):
    """Pin the page's clock (only the page's) to ``moment``."""
    monkeypatch.setattr(
        schedule_entry_views, "timezone", SimpleNamespace(now=lambda: moment)
    )


def test_a_new_time_that_has_passed_today_is_refused(auth_service, google, monkeypatch):
    """Re-verify N1: at 8:00 AM on the 18th its 9:00 AM reminder is unsent;
    the whole series at 7:00 AM would add it at a time already past, so it
    would send at once. Refused at the time, naming the date and time."""
    store = auth_service.store
    series_deployment(store)
    pin(monkeypatch, datetime(2054, 10, 18, 12, tzinfo=UTC))
    browser, _ = signed_in()
    last = reminder(store, LAST)
    path = f"/admin/campaign/schedules/{last}/"
    # Not missed yet: The whole series is offered.
    assert "Unavailable:" not in unescape(browser.get(path).content.decode())
    before = ConfigurationChangeRequest.objects.count()
    refused = post(
        browser, path, edit_post(store, last, time="07:00") | {"series-scope": "series"}
    )
    assert refused.status_code == 400
    assert (
        "the reminder of this series on October 18, 2054 would send at 7:00 AM, "
        "which has already passed" in unescape(refused.content.decode())
    )
    errors = refused.context["entry"].errors.as_data()["time"]
    assert [error.code for error in errors] == ["series_time_passed"]
    assert ConfigurationChangeRequest.objects.count() == before


def test_the_edited_reminder_itself_may_move_into_the_past(
    auth_service, google, monkeypatch
):
    """This and following from the 18th at 7:00 AM: only the edited reminder
    lands in the past, as an ordinary Edit may; the 25th is still ahead."""
    store = auth_service.store
    series_deployment(store)
    pin(monkeypatch, datetime(2054, 10, 18, 12, tzinfo=UTC))
    browser, _ = signed_in()
    first = reminder(store, FIRST)
    review = post(
        browser,
        f"/admin/campaign/schedules/{first}/",
        edit_post(store, first, time="07:00") | {"series-scope": "following"},
    )
    assert review.status_code == 200, review.content
