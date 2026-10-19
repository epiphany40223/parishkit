"""The Campaign calendar against the real configuration (#469, #1521).

The draft's Initial invitation (October 1) and reminder (October 25) come
from the New and Edit scheduled email tests' setup; the calendar lists them
with the same names, statuses and Edit pages as the scheduled emails list,
and its Add on a day opens New scheduled email with that send date. Its
Move… (#1521) posts a new date to the send's Edit page, which reviews it
exactly as Edit does.
"""

import re
from datetime import timedelta
from html import unescape
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import (
    campaign_wizard,
    schedule_calendar_views,
    schedule_entry_views,
)
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.series_edits import rule_text
from parishkit.stewardship.campaigns.models import ScheduleDefinition

from ..campaign_factory import campaign as campaign_record
from ..content_factory import content
from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_admin_navigation_postgresql import flow_steps
from .test_campaign_series_postgresql import FIRST, RULE, reminder, series_deployment
from .test_campaign_views_postgresql import apply, post
from .test_parish_views_postgresql import token
from .test_schedule_entry_postgresql import LIST, NEW, definition, entry
from .test_schedule_entry_postgresql import setup as draft_with_schedules
from .test_schedule_views_postgresql import pending

pytestmark = pytest.mark.django_db(transaction=True)

CALENDAR = "/admin/campaign/schedules/calendar/"


def test_calendar_lists_each_send_and_changes_nothing(auth_service, google):
    """Every send by date, linked to Edit; reading it records no change."""
    store = auth_service.store
    draft_with_schedules(store)
    browser, _ = signed_in()
    before = ConfigurationChangeRequest.objects.count()
    response = browser.get(CALENDAR)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    page = unescape(response.content.decode())
    assert "<h1>Campaign calendar</h1>" in page
    assert "<title>Campaign calendar" in page
    assert "Campaign dates: " in page
    assert 'data-start="2054-10-01" data-end="2054-10-31"' in page
    assert page.count("data-calendar-send ") == 2
    reminder = definition("reminder")
    assert f'data-edit-url="/admin/campaign/schedules/{reminder.pk}/"' in page
    assert 'aria-label="Edit Reminder 1"' in page
    assert "data-local-instant data-compact" in page
    assert "calendar-v1.js" in page
    # Its only forms are Move…'s, which posts to a send's Edit page once the
    # script names it, and the review dialog's Apply, which confirms on the
    # list as Edit's does (#1521).
    assert re.findall(
        r'<form method="post" action="(/admin/campaign/[^"]*)"', page
    ) == [LIST]
    assert flow_steps(response.content) is None
    # The trail is the way back (navigation rule 3), not a link of its own
    # (#521).
    assert f'<li><a href="{LIST}">Dates and mail schedules</a></li>' in page
    # While the Campaign setup stepper applies, its action row is the one
    # way back to its step (#1404), and the page adds no link of its own.
    assert page.count("Return to Dates and mail schedules") == 1
    actions = page.split("data-campaign-step-actions", 1)[1]
    assert "Return to Dates and mail schedules" in actions.split("</div>", 1)[0]
    assert ConfigurationChangeRequest.objects.count() == before
    # The list links it; the calendar takes only its own parameters and no
    # POST.
    assert f'href="{CALENDAR}"' in unescape(browser.get(LIST).content.decode())
    assert browser.get(f"{CALENDAR}?page=2").status_code == 400
    assert browser.post(CALENDAR).status_code in {403, 405}


def test_a_schedule_that_has_run_is_listed_without_edit(
    auth_service, google, monkeypatch
):
    """Once its time has passed with planned work, the invitation is read-only."""
    store = auth_service.store
    draft_with_schedules(store)
    browser, _ = signed_in()
    initial = definition("initial")
    pending(initial, uuid4())
    later = initial.current_revision.due_at + timedelta(hours=1)
    monkeypatch.setattr(
        schedule_calendar_views, "timezone", SimpleNamespace(now=lambda: later)
    )
    page = unescape(browser.get(CALENDAR).content.decode())
    assert 'data-schedule-state="sending"' in page
    assert f'data-edit-url="/admin/campaign/schedules/{initial.pk}/"' not in page
    assert 'aria-label="Edit Initial invitation"' not in page
    assert 'aria-label="Edit Reminder 1"' in page


def test_the_calendar_is_for_administrators_signed_in(auth_service, google, client):
    """Signed out, the calendar shows nothing of the campaign."""
    draft_with_schedules(auth_service.store)
    response = client.get(CALENDAR)
    assert response.status_code in {302, 401, 403}
    assert "data-calendar-send" not in response.content.decode()


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_an_admin_without_configure_is_refused(auth_service, google, role):
    """Staff and Ministry leaders lack CONFIGURE, so the calendar refuses them."""
    store = auth_service.store
    draft_with_schedules(store)
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=(role,)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    response = browser.get(CALENDAR)
    assert response.status_code == 403
    assert "data-calendar-send" not in response.content.decode()


def test_add_on_a_day_opens_new_with_that_date_and_returns_here(
    auth_service, google, monkeypatch
):
    """The calendar's Add (#1521) is New with the day filled in; Apply comes back.

    The day is the only thing filled in, so the review is New's: a reminder
    added from it is reviewed and applied exactly as from the list, and the
    calendar keeps the reserved status line the dialog ends on.

    Outside the campaign wizard (``shown`` says so here, as a live campaign
    would): inside it New saves directly, with no Cancel or review dialog
    of its own (#1404 PR 4b).
    """
    monkeypatch.setattr(campaign_wizard, "shown", lambda *_, **__: False)
    store = auth_service.store
    _, emails = draft_with_schedules(store)
    browser, _ = signed_in()
    calendar = unescape(browser.get(CALENDAR).content.decode())
    assert f'data-add-url="{NEW}"' in calendar
    assert 'data-add-name="Add a scheduled email on {date}"' in calendar
    assert re.search(
        r'<p class="review-outcome" id="schedules-outcome" role="status" '
        r'tabindex="-1" data-review-outcome></p>',
        calendar,
    )
    response = browser.get(f"{NEW}?date=2054-10-12")
    assert response.status_code == 200
    page = unescape(response.content.decode())
    # Drawn as a reminder on that day (the page script would clear a date
    # with no mail type).
    assert re.search(
        r'<input type="date" name="schedule-date" value="2054-10-12"', page
    )
    assert '<option value="reminder" selected>' in page
    # Cancel and the dialog's return are the calendar on that day's month
    # (#1656); Apply still confirms on the list, and the page posts to its
    # own path.
    assert f'<a href="{CALENDAR}?month=2054-10">Cancel</a>' in page
    dialog = page[page.index('<dialog id="schedule-review"') :]
    assert f'data-review-return="{CALENDAR}?month=2054-10"' in dialog
    assert 'data-review-outcome="schedules-outcome"' in dialog
    assert f'<form method="post" action="{LIST}" data-review-apply>' in dialog
    assert f'action="{NEW}" id="schedule-entry-form"' in page
    # Plain New is unchanged: blank, back to the list.
    plain = unescape(browser.get(NEW).content.decode())
    assert 'name="schedule-date" value=' not in plain
    assert f'<a href="{LIST}">Cancel</a>' in plain
    review = post(
        browser,
        NEW,
        entry(
            store,
            kind="reminder",
            date="2054-10-12",
            time="9am",
            template_version=emails["reminder"]["id"],
        ),
    )
    assert review.status_code == 200, review.content
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    assert ScheduleDefinition.objects.filter(
        kind="reminder",
        current_revision__isnull=False,
        current_revision__values__date="2054-10-12",
    ).exists()


@pytest.mark.parametrize(
    "query",
    [
        "date=2054-13-01",
        "date=20541012",
        "date=",
        "date=2054-10-12&date=2054-10-13",
        "date=2054-10-12&sort=when",
    ],
)
def test_new_refuses_an_add_day_that_is_not_one_date(auth_service, google, query):
    """Only one ISO day is taken; anything else is an unknown address."""
    draft_with_schedules(auth_service.store)
    browser, _ = signed_in()
    before = ConfigurationChangeRequest.objects.count()
    assert browser.get(f"{NEW}?{query}").status_code == 400
    assert ConfigurationChangeRequest.objects.count() == before


def test_edit_takes_no_add_day(auth_service, google):
    """Edit's date is the saved schedule's; ?date= is refused there."""
    draft_with_schedules(auth_service.store)
    browser, _ = signed_in()
    reminder = definition("reminder")
    path = f"/admin/campaign/schedules/{reminder.pk}/"
    assert browser.get(f"{path}?date=2054-10-12").status_code == 400


def test_edit_from_the_calendar_returns_to_its_month(auth_service, google, monkeypatch):
    """?calendar=<month> (#1656) points Cancel and Apply at that month.

    Outside the campaign wizard, as the Add test above is.
    """
    monkeypatch.setattr(campaign_wizard, "shown", lambda *_, **__: False)
    store = auth_service.store
    draft_with_schedules(store)
    browser, _ = signed_in()
    path = f"/admin/campaign/schedules/{definition('reminder').pk}/"
    page = unescape(browser.get(f"{path}?calendar=2054-11").content.decode())
    back = f"{CALENDAR}?month=2054-11"
    assert f'<a href="{back}">Cancel</a>' in page
    assert f'data-review-return="{back}"' in page
    assert f'data-review-return="{LIST}"' not in page
    plain = unescape(browser.get(path).content.decode())
    assert f'data-review-return="{LIST}"' in plain
    for refused in (
        f"{path}?calendar=2054-13",
        f"{path}?calendar=",
        f"{path}?date=2054-10-12",
    ):
        assert browser.get(refused).status_code == 400, refused
    # New from a day's Add returns to that day's month.
    added = unescape(browser.get(f"{NEW}?date=2054-10-12").content.decode())
    assert f'<a href="{CALENDAR}?month=2054-10">Cancel</a>' in added


def test_the_calendar_takes_only_a_well_formed_month(auth_service, google):
    """?month= (#1656) is the page script's; a malformed one is an unknown
    address."""
    store = auth_service.store
    draft_with_schedules(store)
    browser, _ = signed_in()
    page = browser.get(f"{CALENDAR}?month=2054-11")
    assert page.status_code == 200
    assert b"data-calendar-step" in page.content
    # A month outside the campaign is the script's to move to the nearer end.
    assert browser.get(f"{CALENDAR}?month=2060-01").status_code == 200
    for month in ("2054-13", "2054-1", "november", ""):
        assert browser.get(f"{CALENDAR}?month={month}").status_code == 400, month
    assert browser.get(f"{CALENDAR}?month=2054-10&month=2054-11").status_code == 400


def test_every_email_by_date_shows_upcoming_and_sorts(
    auth_service, google, monkeypatch
):
    """Show (#1656) leaves out a past email by default and Include past emails
    lists it; When and Schedule sort the table, and each keeps the other's
    choice. The grid's own list always holds every send."""
    store = auth_service.store
    draft_with_schedules(store)
    browser, _ = signed_in()
    initial = definition("initial")
    later = initial.current_revision.due_at + timedelta(hours=1)
    monkeypatch.setattr(
        schedule_calendar_views, "timezone", SimpleNamespace(now=lambda: later)
    )

    def table(query=""):
        page = unescape(browser.get(f"{CALENDAR}{query}").content.decode())
        source = page.split("data-calendar-source", 1)[1].split("</ul>", 1)[0]
        body = page.split('<table class="data-table whole-words calendar-sends">')[1]
        return page, source, re.findall(r'<th scope="row">([^<]+)</th>', body)

    page, source, rows = table()
    assert source.count("data-calendar-send ") == 2
    assert rows == ["Reminder 1"]
    assert 'aria-current="page">Upcoming emails</a>' in page
    assert f'href="{CALENDAR}?show=all&sort=when#table"' in page
    assert 'href="?show=upcoming&sort=-when#table"' in page
    _, source, rows = table("?show=all")
    assert source.count("data-calendar-send ") == 2
    assert rows == ["Initial invitation", "Reminder 1"]
    _, _, rows = table("?show=all&sort=-when")
    assert rows == ["Reminder 1", "Initial invitation"]
    page, _, rows = table("?show=all&sort=-schedule")
    assert rows == ["Reminder 1", "Initial invitation"]
    assert 'href="?show=all&sort=schedule#table"' in page
    assert f'href="{CALENDAR}?show=upcoming&sort=-schedule#table"' in page
    for refused in ("?show=past", "?sort=email", "?show=all&show=upcoming"):
        assert browser.get(f"{CALENDAR}{refused}").status_code == 400, refused


def move(store, zone="America/New_York", **fields):
    """The calendar's Move… post (#1521): the new date, the zone, the version."""
    return {
        "action": "preview",
        "move": "yes",
        "base_digest": store.active().digest,
        "zone": zone,
        **{f"schedule-{name}": value for name, value in fields.items()},
    }


def review_text(response):
    """A review fragment without its signed preview, which differs each time."""
    assert response.status_code == 200, response.content
    return re.sub(
        r'name="preview" value="[^"]+"', "", unescape(response.content.decode())
    )


def test_the_calendar_offers_move_on_dated_sends_with_edits_review_dialog(
    auth_service, google
):
    """Each dated send carries its Edit page for Move… and the dialog is drawn.

    The Move dialog's form is Edit's own (its id, so a refusal answered with
    Edit's page marks it), posts move=yes with the version and the zone, says
    a refusal in its own line, and is gated per send; the review dialog
    redraws the page in place (no return address) and ends on the
    calendar's status line.
    """
    store = auth_service.store
    draft_with_schedules(store)
    browser, _ = signed_in()
    page = unescape(browser.get(CALENDAR).content.decode())
    for kind in ("initial", "reminder"):
        edit = f"/admin/campaign/schedules/{definition(kind).pk}/"
        assert f'data-edit-url="{edit}" data-move-url="{edit}"' in page
    assert page.count(' data-email="') == 2
    assert 'data-move-name="Move {name}…"' in page
    region = page.split(
        '<div id="calendar-source" data-in-place-region data-in-place-own>'
    )[1]
    dialog = region.split("</dialog>", 1)[0]
    assert '<dialog id="calendar-move"' in dialog
    assert (
        '<form method="post" id="schedule-entry-form" '
        'data-review-open="schedule-review" '
        'data-review-refused="calendar-move-refusal" '
        'data-require-change="calendar-move-version">'
    ) in dialog
    for field in (
        '<input type="hidden" name="action" value="preview">',
        '<input type="hidden" name="move" value="yes">',
        f'<input type="hidden" name="base_digest" value="{store.active().digest}">',
        '<input type="hidden" name="zone" value="" data-browser-zone>',
        '<input type="date" name="schedule-date" id="id_schedule-date" required '
        "data-browser-zone-field>",
        'id="calendar-move-refusal" role="status"',
        "Review the move",
        "Choose another date to review the move.",
    ):
        assert field in dialog, field
    review = page[page.index('<dialog id="schedule-review"') :]
    assert 'data-review-return=""' in review
    assert 'data-review-outcome="schedules-outcome"' in review
    assert f'<form method="post" action="{LIST}" data-review-apply>' in review


def test_a_move_is_edits_own_review_and_keeps_the_browser_local_time(
    auth_service, google
):
    """Move… answers exactly what Edit answers for the new date (#1521).

    A browser in London sees the October 25 reminder (9:00 AM in New York)
    at 1:00 PM, its clocks having gone back that morning. Moved to October
    20, it keeps 1:00 PM in London, which is still on summer time there: 8:00
    AM in New York. Applied, only the reminder's date and time change, and
    the calendar lists it on its new day.
    """
    store = auth_service.store
    _, emails = draft_with_schedules(store)
    browser, _ = signed_in()
    reminder_id = definition("reminder").pk
    edit = f"/admin/campaign/schedules/{reminder_id}/"
    london = "Europe/London"
    moved = post(browser, edit, move(store, london, date="2054-10-20"))
    edited = post(
        browser,
        edit,
        entry(
            store,
            london,
            date="2054-10-20",
            time="13:00",
            template_version=emails["reminder"]["id"],
            drawn_at="2054-10-25T13:00:00+00:00",
        ),
    )
    assert review_text(moved) == review_text(edited)
    assert "leaves its series" not in review_text(moved)
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(moved)}))
    saved = definition("reminder").current_revision.values
    assert (saved["date"], saved["time"]) == ("2054-10-20", "08:00:00")
    assert saved["template_version"] == emails["reminder"]["id"]
    page = unescape(browser.get(CALENDAR).content.decode())
    assert 'datetime="2054-10-20T12:00:00+00:00"' in page


def test_a_move_edit_refuses_is_refused_the_same_way(auth_service, google):
    """A day outside the campaign is Edit's field refusal; nothing recorded."""
    store = auth_service.store
    draft_with_schedules(store)
    browser, _ = signed_in()
    edit = f"/admin/campaign/schedules/{definition('reminder').pk}/"
    before = ConfigurationChangeRequest.objects.count()
    refused = post(browser, edit, move(store, date="2054-11-12"))
    assert refused.status_code == 400
    page = unescape(refused.content.decode())
    # Edit's page drawn again, its form and summary for the dialog's line.
    assert 'id="schedule-entry-form"' in page
    assert "data-error-summary" in page
    assert "Choose a date within the campaign" in page
    assert ConfigurationChangeRequest.objects.count() == before


@pytest.mark.parametrize(
    "extra",
    [
        {"schedule-time": "10:00"},
        {"schedule-template_version": "x"},
        {"series-scope": "this"},
        {"move": "no"},
        {"action": "save"},
    ],
)
def test_a_move_takes_only_its_own_fields(auth_service, google, extra):
    """Anything but the date, zone and version is an unexpected field."""
    store = auth_service.store
    draft_with_schedules(store)
    browser, _ = signed_in()
    edit = f"/admin/campaign/schedules/{definition('reminder').pk}/"
    before = ConfigurationChangeRequest.objects.count()
    response = post(browser, edit, move(store, date="2054-10-20") | extra)
    assert response.status_code == 400
    assert "data-review-answer" not in response.content.decode()
    assert ConfigurationChangeRequest.objects.count() == before


def test_new_takes_no_move(auth_service, google):
    """Only an existing schedule moves; New refuses the field."""
    store = auth_service.store
    draft_with_schedules(store)
    browser, _ = signed_in()
    assert post(browser, NEW, move(store, date="2054-10-20")).status_code == 400


def test_a_missed_send_or_a_digest_does_not_move(auth_service, google, monkeypatch):
    """Not movable (#1521): the calendar offers no Move… and the server refuses.

    The Initial invitation's time has passed without work (missed), so it
    still links to Edit but not to Move…; a Daily Admin digest never moves.
    Both refusals are plain ones for the review dialog, recording nothing.
    """
    store = auth_service.store
    _, _ = draft_with_schedules(store)
    campaign_id = store.active().document()["sections"]["campaigns"][0]["id"]
    digest = content(campaign_id, kind="email", slot="daily_digest", subject="Digest")
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "content", **digest}],
        ).state
        == "applied"
    )
    browser, _ = signed_in()
    review = post(
        browser,
        NEW,
        entry(store, kind="daily_digest", time="09:30", template_version=digest["id"]),
    )
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(review)}))
    initial = definition("initial")
    later = initial.current_revision.due_at + timedelta(hours=1)
    clock = SimpleNamespace(now=lambda: later)
    monkeypatch.setattr(schedule_calendar_views, "timezone", clock)
    monkeypatch.setattr(schedule_entry_views, "timezone", clock)
    page = unescape(browser.get(CALENDAR).content.decode())
    first = f"/admin/campaign/schedules/{initial.pk}/"
    assert f'data-edit-url="{first}"' in page
    assert f'data-move-url="{first}"' not in page
    daily = f"/admin/campaign/schedules/{definition('daily_digest').pk}/"
    assert f'data-edit-url="{daily}?send=' in page
    assert f'data-move-url="{daily}' not in page
    before = ConfigurationChangeRequest.objects.count()
    for path in (first, daily):
        refused = post(browser, path, move(store, date="2054-10-20"))
        assert refused.status_code == 403, path
        assert "can't be moved from the calendar" in unescape(refused.content.decode())
    assert ConfigurationChangeRequest.objects.count() == before


def test_a_series_reminder_moved_off_its_rule_leaves_it_and_says_so(
    auth_service, google
):
    """A move is "This reminder only" (#1521): off the rule it leaves the series.

    The series sends every Sunday; October 11 is one of its days, so a move
    there keeps the reminder in it and says nothing, while October 19, a
    Monday, takes it out and the review says so.
    """
    store = auth_service.store
    series_deployment(store)
    browser, _ = signed_in()
    first = reminder(store, FIRST)
    edit = f"/admin/campaign/schedules/{first}/"
    line = f"This reminder leaves its series ({rule_text(RULE)}), since the series"
    kept = post(browser, edit, move(store, date="2054-10-11"))
    assert line not in review_text(kept)
    left = post(browser, edit, move(store, date="2054-10-19"))
    assert line in review_text(left)
    apply(store, post(browser, LIST, {"action": "confirm", "preview": token(left)}))
    series = store.active().document()["sections"]["schedule_series"]
    assert first not in series[0]["values"]["schedules"]


def test_a_move_into_the_repeated_hour_is_refused_at_the_date(auth_service, google):
    """The kept time can fall in New York's repeated hour on the new day (#1521).

    The reminder sends at 2:30 AM New York time on October 31 (06:30 UTC),
    1:30 AM in Chicago. Moved there to November 1, 1:30 AM in Chicago is
    New York's second 1:30 AM, which a schedule cannot keep exactly: the
    move is refused at its date, in words the Move dialog can act on, and
    nothing is recorded.
    """
    store = auth_service.store
    row = campaign_record(end_date="2054-11-05")
    draft_with_schedules(store, row, date="2054-10-31", time="02:30:00")
    browser, _ = signed_in()
    edit = f"/admin/campaign/schedules/{definition('reminder').pk}/"
    before = ConfigurationChangeRequest.objects.count()
    refused = post(browser, edit, move(store, "America/Chicago", date="2054-11-01"))
    assert refused.status_code == 400
    page = unescape(refused.content.decode())
    assert "Choose another day, or change the time on its Edit page." in page
    assert re.search(r'name="schedule-date"[^>]*aria-invalid="true"', page)
    assert ConfigurationChangeRequest.objects.count() == before
