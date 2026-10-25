"""Change the repeat of a reminder series from Edit scheduled email (#1808).

Through real Admin sessions and the configuration installer: Edit of a
series' reminder offers Change the repeat with "This and following" and
"The whole series", filled in with the series' rule; Review plans the new
rule with the command line's own ``series_edits.plan`` and checks it with
the command line's own ``admin_changes.checked_rule``; Apply saves the
reminders and series records. Refusals are shown at the rule's fields and
change nothing.
"""

import json
import re
from datetime import UTC, datetime
from html import unescape

import pytest

from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.series_edits import rule_text
from parishkit.stewardship.campaigns.recurrence import canonical_rule

from .auth_builders import signed_in
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
from .test_schedule_entry_postgresql import LIST
from .test_schedule_series_page_postgresql import (
    edit_post,
    pin,
    read_only,
    scope_codes,
    values_of,
)

pytestmark = pytest.mark.django_db(transaction=True)

# October 25, 2054 (LAST) is a Sunday, October 28 a Wednesday.
SUNDAY, WEDNESDAY = 6, 2


def rule_post(start, until, *, frequency="weekly", weekdays=(SUNDAY,), **fields):
    """Change the repeat's fields as the page posts them (prefix ``repeat``)."""
    posted = {
        "series-rule": "on",
        "repeat-frequency": frequency,
        "repeat-start": start,
        "repeat-until": until,
        "repeat-by": "day",
        "repeat-day": "1",
        "repeat-ordinal": "1",
        "repeat-month_weekday": "0",
    }
    if weekdays:
        posted["repeat-weekdays"] = [str(day) for day in weekdays]
    return posted | {f"repeat-{name}": value for name, value in fields.items()}


def rule_codes(response, field):
    """The closed codes of a refused page's errors at one rule field (#1437)."""
    form = response.context["series"]["rule"]
    return [error.code for error in form.errors.as_data().get(field, [])]


def test_edit_draws_change_the_repeat_filled_in_with_the_rule(auth_service, google):
    """The box shows only for the two series scopes; the rule's fields are the
    saved rule's, From at the first date the default scope changes, and each
    scope carries its first date and the earliest From it accepts."""
    store = auth_service.store
    series_deployment(store)
    browser, _ = signed_in()
    page = unescape(
        browser.get(
            f"/admin/campaign/schedules/{reminder(store, LAST)}/"
        ).content.decode()
    )
    # Greyed while This reminder only is chosen, so a scope moves nothing.
    assert 'data-enabled-when="series-scope!=this" disabled>' in page
    assert 'name="series-rule"' in page and "Change the repeat" in page
    assert re.search(r"<div data-series-rule [^>]*>", page)[0].endswith(" hidden>")
    assert 'name="repeat-frequency"' in page and 'name="repeat-sends"' not in page
    assert '<option value="weekly" selected>' in page
    assert f'value="{SUNDAY}" checked' in page
    assert (
        'name="repeat-start" type="date" data-repeat="from" value="2054-10-25"' in page
    )
    assert (
        f'name="repeat-until" type="date" data-repeat="until" value="{RULE["end"]}"'
        in (page)
    )
    # This and following from the 25th keeps the 18th in the series, so its
    # new rule may not start earlier; the whole series starts on the 18th.
    assert (
        'value="following" id="id_series-scope_1" data-rule-from="2054-10-25" ' in page
    )
    assert (
        'data-rule-min="2054-10-25" data-rule-min-hint="Start the new repeat on' in page
    )
    assert 'value="series" id="id_series-scope_2" data-rule-from="2054-10-18" ' in page
    assert 'data-rule-from="2054-10-18" data-rule-min="" ' in page
    # The page's date list is drawn for the rule.
    assert "data-repeat-dates-url" in page and "data-repeat-summary" in page


def test_a_new_rule_from_this_reminder_splits_the_series(auth_service, google):
    """This and following from the 25th on Sundays and Wednesdays: the 18th
    stays under the old rule, cut short; the 25th stays as it is and the
    28th is added, both in the new series."""
    store = auth_service.store
    _, identifier = series_deployment(store)
    browser, _ = signed_in()
    last = reminder(store, LAST)
    review = post(
        browser,
        f"/admin/campaign/schedules/{last}/",
        edit_post(store, last)
        | {"series-scope": "following"}
        | rule_post(LAST, "2054-10-31", weekdays=(SUNDAY, WEDNESDAY)),
    )
    assert review.status_code == 200, review.content
    text = unescape(review.content.decode())
    new = canonical_rule(
        {
            "frequency": "weekly",
            "start": LAST,
            "end": "2054-10-31",
            "weekdays": [SUNDAY, WEDNESDAY],
        }
    )
    earlier = rule_text(canonical_rule(RULE | {"end": "2054-10-24"}))
    assert f"Reminders before October 25, 2054 stay in this series: {earlier}." in text
    assert (
        f"From October 25, 2054, the changed reminders are a new series: "
        f"{rule_text(new)}." in text
    )
    assert "1 reminder of this series stays as it is." in text
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    kept, split = sorted(remembered(store), key=lambda row: row["id"] != identifier)
    assert kept["values"]["schedules"] == [reminder(store, FIRST)]
    assert kept["values"]["rule"]["end"] == "2054-10-24"
    added = reminder(store, "2054-10-28")
    assert split["values"]["rule"] == new
    assert split["values"]["schedules"] == sorted([last, added])
    # The kept reminder is the same schedule; the added one takes its time
    # and email.
    assert reminder(store, LAST) == last
    assert values_of(store, added)["time"] == values_of(store, last)["time"]
    assert (
        values_of(store, added)["template_version"]
        == values_of(store, last)["template_version"]
    )


def test_the_whole_series_takes_a_new_rule_and_time(auth_service, google):
    """Nothing sent: every day October 29–31 at 10:00 AM replaces both
    reminders, and the series itself takes the new rule."""
    store = auth_service.store
    _, identifier = series_deployment(store)
    browser, _ = signed_in()
    first = reminder(store, FIRST)
    review = post(
        browser,
        f"/admin/campaign/schedules/{first}/",
        edit_post(store, first, time="10:00")
        | {"series-scope": "series"}
        | rule_post("2054-10-29", "2054-10-31", frequency="daily", weekdays=()),
    )
    assert review.status_code == 200, review.content
    text = unescape(review.content.decode())
    new = canonical_rule(
        {"frequency": "daily", "start": "2054-10-29", "end": "2054-10-31"}
    )
    assert f"This series now repeats: {rule_text(new)}." in text
    assert "stays as it is" not in text and "stay in this series" not in text
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    [row] = remembered(store)
    days = ["2054-10-29", "2054-10-30", "2054-10-31"]
    assert row["id"] == identifier and row["values"]["rule"] == new
    assert row["values"]["schedules"] == sorted(reminder(store, day) for day in days)
    assert {values_of(store, reminder(store, day))["time"] for day in days} == {
        "10:00:00"
    }
    dates = {
        row["values"]["date"]
        for row in store.active().document()["sections"]["schedules"]
        if row["values"]["kind"] == "reminder"
    }
    assert FIRST not in dates and LAST not in dates


@pytest.mark.parametrize(
    ("scope", "rule", "field", "code", "words"),
    [
        # This and following from the 25th keeps the 18th: From before it.
        (
            "following",
            rule_post("2054-10-20", "2054-10-31"),
            "start",
            "repeat_rule_start",
            "Start the new repeat on or after October 25, 2054",
        ),
        # The 31st is not in November: the command line's skipped month.
        (
            "following",
            rule_post("2054-10-25", "2054-11-30", frequency="monthly", day="31"),
            "day",
            "repeat_skips",
            "The day of the month is not in November 2054",
        ),
        # Over 30 dates, the command line's limit.
        (
            "series",
            rule_post("2054-10-25", "2054-12-31", frequency="daily", weekdays=()),
            "until",
            "repeat_count",
            "A series repeats on 1 to 30 dates; this rule gives 68.",
        ),
        # A rule expand refuses: no day of the week.
        (
            "following",
            rule_post("2054-10-25", "2054-10-31", weekdays=()),
            "weekdays",
            "repeat_weekdays",
            "Choose at least one day of the week.",
        ),
    ],
)
def test_a_refused_rule_is_shown_at_its_field_and_changes_nothing(
    auth_service, google, scope, rule, field, code, words
):
    """Each refusal is the page drawn again (400) with the words at the rule
    field and its closed code; nothing is recorded."""
    store = auth_service.store
    series_deployment(store)
    browser, _ = signed_in()
    last = reminder(store, LAST)
    before = ConfigurationChangeRequest.objects.count()
    refused = post(
        browser,
        f"/admin/campaign/schedules/{last}/",
        edit_post(store, last) | {"series-scope": scope} | rule,
    )
    assert refused.status_code == 400
    assert rule_codes(refused, field) == [code]
    page = unescape(refused.content.decode())
    assert words in page
    # Drawn again as posted: the box ticked and the rule shown.
    assert 'data-enabled-when="series-scope!=this" checked>' in page
    assert not re.search(r"<div data-series-rule [^>]*>", page)[0].endswith(" hidden>")
    assert ConfigurationChangeRequest.objects.count() == before


def test_change_the_repeat_needs_a_series_scope(auth_service, google):
    """An old or crafted page posting the box with This reminder only is
    refused at the choice; nothing is recorded."""
    store = auth_service.store
    series_deployment(store)
    browser, _ = signed_in()
    last = reminder(store, LAST)
    before = ConfigurationChangeRequest.objects.count()
    refused = post(
        browser,
        f"/admin/campaign/schedules/{last}/",
        edit_post(store, last, date=LAST, time="10:30")
        | {"series-scope": "this"}
        | rule_post(LAST, "2054-10-31"),
    )
    assert refused.status_code == 400
    form = refused.context["series"]["form"]
    assert [error.code for error in form.errors.as_data()["scope"]] == ["rule_scope"]
    assert ConfigurationChangeRequest.objects.count() == before


def test_an_unchanged_rule_is_refused_like_an_unchanged_edit(auth_service, google):
    """The box ticked with the rule as drawn and nothing else changed: the
    dialog's plain refusal, naming the repeat; nothing is recorded."""
    store = auth_service.store
    series_deployment(store)
    browser, _ = signed_in()
    last = reminder(store, LAST)
    before = ConfigurationChangeRequest.objects.count()
    same = browser.post(
        f"/admin/campaign/schedules/{last}/",
        edit_post(store, last)
        | {"series-scope": "following"}
        | rule_post(LAST, RULE["end"])
        | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value},
        headers={"X-Requested-With": "fetch"},
    )
    assert same.status_code == 400
    assert json.loads(same.content)["refusal"]["message"] == (
        "Nothing has changed. Change the time, email or repeat, then choose "
        "Review changes."
    )
    assert ConfigurationChangeRequest.objects.count() == before


def test_a_browser_a_day_ahead_is_refused_for_a_new_rule(auth_service, google):
    """On Kiritimati (UTC+14) a 9:00 AM New York send is the next day, so
    the rule's calendar days would be other moments than the page listed:
    refused at the choice even with the time left as shown."""
    store = auth_service.store
    series_deployment(store)
    browser, _ = signed_in()
    last = reminder(store, LAST)
    before = ConfigurationChangeRequest.objects.count()
    # 9:00 AM in New York is 3:00 AM the next day on Kiritimati.
    refused = post(
        browser,
        f"/admin/campaign/schedules/{last}/",
        edit_post(store, last, zone="Pacific/Kiritimati", time="03:00")
        | {"series-scope": "following"}
        | rule_post(LAST, "2054-10-31", weekdays=(SUNDAY, WEDNESDAY)),
    )
    assert refused.status_code == 400
    assert "would send on another day" in unescape(refused.content.decode())
    assert scope_codes(refused) == ["series_edit_refused"]
    assert ConfigurationChangeRequest.objects.count() == before


def test_the_whole_series_after_a_sent_reminder_starts_after_it(
    auth_service, google, monkeypatch
):
    """With the 18th sent, The whole series cuts at the 25th, so a rule from
    the 20th is refused at From; one from the 25th splits the series."""
    store = auth_service.store
    _, identifier = series_deployment(store)
    first = reminder(store, FIRST)
    read_only(monkeypatch, first)
    browser, _ = signed_in()
    last = reminder(store, LAST)
    path = f"/admin/campaign/schedules/{last}/"
    refused = post(
        browser,
        path,
        edit_post(store, last)
        | {"series-scope": "series"}
        | rule_post("2054-10-20", "2054-10-31"),
    )
    assert refused.status_code == 400
    assert rule_codes(refused, "start") == ["repeat_rule_start"]
    # The last Sunday of the month: a monthly rule by weekday.
    review = post(
        browser,
        path,
        edit_post(store, last)
        | {"series-scope": "series"}
        | rule_post(
            LAST,
            "2054-10-31",
            frequency="monthly",
            weekdays=(),
            by="weekday",
            ordinal="-1",
            month_weekday=str(SUNDAY),
        ),
    )
    assert review.status_code == 200, review.content
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    kept, split = sorted(remembered(store), key=lambda row: row["id"] != identifier)
    assert kept["values"]["schedules"] == [first]
    assert split["values"]["rule"]["monthly"] == {"ordinal": -1, "weekday": SUNDAY}
    assert split["values"]["schedules"] == [last]


def test_a_new_rule_with_a_date_outside_the_campaign_is_refused(auth_service, google):
    """Sundays through November 8 give November 1 and 8, after the campaign
    ends: the page's script holds Review, so only a crafted post meets this.
    Refused (400) naming each date, with the window's closed code; nothing is
    recorded."""
    store = auth_service.store
    series_deployment(store)
    browser, _ = signed_in()
    last = reminder(store, LAST)
    before = ConfigurationChangeRequest.objects.count()
    refused = post(
        browser,
        f"/admin/campaign/schedules/{last}/",
        edit_post(store, last)
        | {"series-scope": "series"}
        | rule_post(LAST, "2054-11-08"),
    )
    assert refused.status_code == 400
    page = unescape(refused.content.decode())
    assert "November 1, 2054: Choose a date within the campaign" in page
    assert "November 8, 2054: Choose a date within the campaign" in page
    errors = refused.context["entry"].errors.as_data()["__all__"]
    assert [error.code for error in errors] == ["outside_campaign"] * 2
    assert ConfigurationChangeRequest.objects.count() == before


def test_a_new_rule_with_a_date_already_past_is_refused(
    auth_service, google, monkeypatch
):
    """On October 10, daily from October 8 gives two dates already past,
    which would send at once: refused (400) at From with its closed code;
    nothing is recorded."""
    store = auth_service.store
    series_deployment(store)
    pin(monkeypatch, datetime(2054, 10, 10, 16, tzinfo=UTC))
    browser, _ = signed_in()
    first = reminder(store, FIRST)
    before = ConfigurationChangeRequest.objects.count()
    refused = post(
        browser,
        f"/admin/campaign/schedules/{first}/",
        edit_post(store, first)
        | {"series-scope": "series"}
        | rule_post("2054-10-08", "2054-10-12", frequency="daily", weekdays=()),
    )
    assert refused.status_code == 400
    assert rule_codes(refused, "start") == ["repeat_time_passed"]
    assert (
        "The reminder on October 8, 2054 at 9:00 AM would send at a time that "
        "has already passed" in unescape(refused.content.decode())
    )
    assert ConfigurationChangeRequest.objects.count() == before
