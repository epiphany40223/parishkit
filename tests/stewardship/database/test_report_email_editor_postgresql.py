"""New and Edit report email on the restricted web login (#1786, item 6).

The editor draws New blank and Edit from the saved report; Review answers
the shared review dialog's fragment, or this page again with each refusal
at its field (400); Apply records the change, which the real installer
applies. A report that sends after the campaign's last day needs the
dialog's tick (decision 9). Edit's Skip selected sends, Send selected
sends and Delete review and apply the same way, each send of a skip or
unskip checked on its own. The live Next sends table answers the next
sends.
Staff may be recipients, and a report to All Staff reaches them (#1597).
Dates and mail schedules lists Family mail only; the Campaign calendar
still lists each report send, linking its Edit page.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.deployment import ServiceRole

from .auth_builders import signed_in
from .campaign_builders import campaign_clock, complete_empty_catchup
from .report_builders import WEEKLY_INSTANT as INSTANT
from .report_builders import add_digest, add_report, set_user
from .test_background_grants_postgresql import task_login
from .test_report_emails_page_postgresql import apply_requests, report_values

pytestmark = pytest.mark.django_db(transaction=True)

# The fixture campaign runs October 1 to 31, 2054; New York time.
ZONE = "America/New_York"


def fields(**values):
    """A complete editor post for a one-time report to All Staff."""
    base = {
        "action": "preview",
        "name": "Staff requests",
        "subject": "",
        "sends": "once",
        "once_date": "2054-10-20",
        "time": "9:00",
        "editor_mode": "basic",
        "as_of": "send_time",
        "recipient": ["group:staff", "address:council@example.org"],
        "block": ["additional_information"],
        "html": "",
        "text": "",
        "zone": ZONE,
    }
    return base | values


def post(browser, path, data):
    """POST ``data`` to ``path`` as the page's script does."""
    digest = SystemConfiguration.objects.get().active_configuration.digest
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.post(
            path,
            {"base_digest": digest} | data,
            HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value,
        )


def token(answer):
    """The signed preview in a review fragment."""
    text = answer.content.decode()
    marker = 'name="preview" value="'
    start = text.index(marker) + len(marker)
    return text[start : text.index('"', start)]


def confirm(browser, path, answer, **extra):
    """Apply a review, as the dialog's Apply does."""
    data = {"action": "confirm", "preview": token(answer)} | extra
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.post(
            path, data, HTTP_X_CSRFTOKEN=browser.cookies["pk_admin_csrf"].value
        )


def reports():
    """The applied report emails, by name."""
    document = SystemConfiguration.objects.get().active_configuration.canonical_document
    return {
        row["values"]["name"]: row
        for row in document["sections"]["schedules"]
        if row["values"]["kind"] == "report"
    }


def started(harness):
    """Sign in an Administrator on a campaign whose sends may run.

    Report emails exist only in the users form of the configuration, which
    Production holds since #952; the fixture converts to it first.
    """
    from .report_builders import users_form

    users_form(harness.service.store)
    harness.campaign.refresh_from_db()
    if harness.campaign.state == "active":
        complete_empty_catchup(harness.campaign, uuid4())
    browser, _ = signed_in()
    return browser


def test_new_reviews_and_adds_a_report_to_staff_and_an_address(
    live_response_service, google
):
    """A one-time report to All Staff and an outside address is added."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        admin = started(harness)
        path = reverse("admin:report_email_new")
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = admin.get(path)
        assert page.status_code == 200 and b"Add the report email" in page.content
        answer = post(admin, path, fields())
        assert answer.status_code == 200, answer.content
        assert b"data-review-answer" in answer.content
        assert b"All Staff (group" in answer.content
        assert b"council@example.org" in answer.content
        done = confirm(admin, path, answer)
        assert done.status_code == 302, done.content
        apply_requests(harness.service.store)
        values = reports()["Staff requests"]["values"]
        assert values["recipients"] == [
            {"group": "staff"},
            {"address": "council@example.org"},
        ]
        assert values["schedule"] == {
            "type": "once",
            "date": "2054-10-20",
            "time": "09:00:00",
        }
        assert values["slot_basis"] == "send_date"


def test_a_refused_review_answers_the_page_with_its_fields(
    live_response_service, google
):
    """No name, no recipient and no block: each is refused at its field."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        admin = started(harness)
        path = reverse("admin:report_email_new")
        answer = post(admin, path, fields(name="", recipient=[], block=[]))
        assert answer.status_code == 400
        text = answer.content.decode()
        for field in (
            "report-name_error",
            "report-recipient_error",
            "report-block_error",
        ):
            assert f'id="{field}"' in text, field
        assert "Choose at least one recipient." in text
        assert "Add at least one block." in text
        # A name another report has is refused at the name.
        add_digest(harness.service.store, harness.campaign, weekly=True)
        taken = reports()
        name = next(iter(taken))
        answer = post(admin, path, fields(name=name.upper()))
        assert answer.status_code == 400
        assert b"Another report email of this campaign has this name." in answer.content


def test_a_send_after_the_campaign_needs_the_tick(live_response_service, google):
    """Daily until the day after the campaign ends sends after it:
    Apply is refused until the dialog's tick is posted (decision 9)."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        admin = started(harness)
        path = reverse("admin:report_email_new")
        data = fields(
            name="Daily",
            sends="repeat",
            preset="daily",
            time="7:00",
            as_of="previous_day_end",
            block=["campaign_totals", "day_by_day"],
            days_shown="7",
        )
        answer = post(admin, path, data)
        assert answer.status_code == 200, answer.content
        assert b"data-after-campaign-tick" in answer.content
        refused = confirm(admin, path, answer)
        assert refused.status_code == 400
        assert "Send after the campaign ends" in refused.json()["refusal"]["message"]
        assert confirm(admin, path, answer, after_campaign="yes").status_code == 302
        apply_requests(harness.service.store)
        values = reports()["Daily"]["values"]
        assert values["schedule"]["frequency"] == "daily"
        assert values["schedule"]["until"] == "day_after_campaign_end"
        assert values["blocks"][1] == {
            "block": "day_by_day",
            "version": 1,
            "options": {"days": 7},
        }


def test_edit_skip_and_delete(live_response_service, google):
    """Edit changes a saved report; Skip selected sends skips two sends and
    Send selected sends makes one of them again; Delete removes it, each
    reviewed and applied."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        identifier = add_digest(harness.service.store, harness.campaign, weekly=True)
        admin = started(harness)
        path = reverse("admin:report_email_edit", args=[identifier])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = admin.get(path)
        assert page.status_code == 200
        text = page.content.decode()
        assert "data-schedule-local=" in text and "Skip selected sends" in text
        assert "Send selected sends" in text and "Will send" in text
        # A skip's Apply redraws the table in place (no return page), and
        # the forms take the new configuration version.
        assert 'id="report-skip-review"' in text
        skip_dialog = text[text.index('id="report-skip-review"') :]
        assert 'data-review-return=""' in skip_dialog[: skip_dialog.index(">")]
        assert 'data-review-region="report-schedule-preview"' in text
        saved = report_values(identifier)
        answer = post(
            admin,
            path,
            fields(
                name=saved["name"],
                sends="repeat",
                editor_mode="advanced",
                frequency="weekly",
                weekdays=["2", "4"],
                from_kind="campaign_start",
                until_kind="campaign_end",
                time="10:30",
                as_of="send_time",
                recipient=["group:administrators", "group:staff"],
                block=["additional_information", "response_funnel"],
            ),
        )
        assert answer.status_code == 200, answer.content
        assert confirm(admin, path, answer).status_code == 302
        apply_requests(harness.service.store)
        values = report_values(identifier)
        assert values["schedule"]["weekdays"] == [2, 4]
        assert {"group": "staff"} in values["recipients"]
        # Skip selected sends: the first two sends still ahead, in one
        # review that names both.
        first, second = page_slots(admin, path)[:2]
        answer = post(admin, path, {"action": "skip_preview", "send": [second, first]})
        assert answer.status_code == 200, answer.content
        assert answer.content.decode().count("data-local-instant") == 2
        assert b"2 sends skipped." in answer.content
        assert confirm(admin, path, answer).status_code == 302
        apply_requests(harness.service.store)
        assert {first, second} <= set(report_values(identifier)["skip_dates"])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            text = admin.get(path).content.decode()
        assert text.count('data-confirm-kind="send"') == 3  # 2 rows and the button
        # Send selected sends: the first one sends again; the other stays
        # skipped.
        answer = post(admin, path, {"action": "unskip_preview", "send": first})
        assert answer.status_code == 200, answer.content
        assert b"Skipped send will send." in answer.content
        assert confirm(admin, path, answer).status_code == 302
        apply_requests(harness.service.store)
        skips = report_values(identifier)["skip_dates"]
        assert second in skips and first not in skips
        answer = post(admin, path, {"action": "delete_preview"})
        assert answer.status_code == 200, answer.content
        assert confirm(admin, path, answer).status_code == 302
        apply_requests(harness.service.store)
        assert report_values(identifier) is None
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            assert admin.get(path).status_code == 404


def page_slots(browser, path):
    """The slot keys of the Edit page's Next sends boxes, in order."""
    import re

    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        text = browser.get(path).content.decode()
    return re.findall(r'name="send" value="([^"]+)"', text)


def test_the_live_preview_answers_the_next_sends(live_response_service, google):
    """The schedule as typed, drawn as the Next sends table."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        admin = started(harness)
        answer = post(
            admin,
            reverse("admin:report_email_schedule_preview"),
            fields(sends="repeat", preset="weekly", preset_weekday="0", time="8:00"),
        )
        assert answer.status_code == 200
        assert b"report-sends" in answer.content and b"Will send" in answer.content
        # New's table has no boxes: there is nothing saved to skip.
        assert b"data-select-row" not in answer.content
        unreadable = post(
            admin, reverse("admin:report_email_schedule_preview"), fields(time="")
        )
        assert b"Fill in the schedule and time" in unreadable.content


def test_staff_may_not_open_the_editor(live_response_service, google):
    """The editor is an Administrator's page (403 for Staff)."""
    from .report_builders import users_form

    harness = live_response_service
    with campaign_clock(INSTANT):
        users_form(harness.service.store)
        set_user(harness.service.store, "staff@example.org", "staff")
        google[0]["email"] = "staff@example.org"
        staff, _ = signed_in()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            assert staff.get(reverse("admin:report_email_new")).status_code == 403


def test_family_mail_and_the_calendar_list_report_emails_apart(
    live_response_service, google
):
    """Dates and mail schedules shows no report email; the Campaign calendar
    lists its sends, each linking its Edit page with the send named."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        identifier = add_digest(harness.service.store, harness.campaign, weekly=True)
        admin = started(harness)
        edit = reverse("admin:report_email_edit", args=[identifier])
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            schedules = admin.get(reverse("admin:schedule_settings"))
            calendar = admin.get(reverse("admin:schedule_calendar") + "?show=all")
        assert schedules.status_code == 200
        assert edit.encode() not in schedules.content
        assert reverse("admin:report_emails").encode() in schedules.content
        assert calendar.status_code == 200
        assert f"{edit}?send=".encode() in calendar.content


def test_a_report_to_all_staff_reaches_them(live_response_service, google):
    """Staff are recipients of the open requests block (#1597, decision 3)."""
    from parishkit.stewardship.reports.report_email_models import (
        ReportEmailRecipient,
    )

    from .report_builders import WEEKLY_BLOCKS, prepare_due

    harness = live_response_service
    store = harness.service.store
    with campaign_clock(INSTANT):
        started(harness)
        set_user(store, "staff@example.org", "staff")
        add_report(
            store,
            harness.campaign,
            name="Staff weekly",
            recipients=[{"group": "staff"}],
            blocks=WEEKLY_BLOCKS,
            schedule={
                "type": "repeat",
                "frequency": "daily",
                "from": "campaign_start",
                "until": "campaign_end",
                "time": "00:15:00",
            },
        )
        prepare_due()
        addresses = set(
            ReportEmailRecipient.objects.filter(kind="group").values_list(
                "address", flat=True
            )
        )
        assert "staff@example.org" in addresses


def refused_at(answer, field, message):
    """Assert a 400 answer shows ``message`` in ``field``'s error line."""
    assert answer.status_code == 400, answer.content
    text = answer.content.decode()
    start = text.index(f'id="report-{field}_error"')
    line = text[start : text.index("</ul>", start)]
    assert message in line, (field, message, line)


def test_recipient_refusals_are_at_the_field(live_response_service, google):
    """An invalid address, a recipient twice (in other letter case too), an
    unknown user and both limits are refused at Recipients (400); a portal
    user's own address, in any letter case, is stored as that user."""
    from .user_builders import user_id

    harness = live_response_service
    store = harness.service.store
    with campaign_clock(INSTANT):
        admin = started(harness)
        set_user(store, "staff@example.org", "staff", name="Pat Doe")
        set_user(store, "lee@example.org", "ministry_leader", name="Lee")
        for index in range(24):
            set_user(store, f"s{index}@example.org", "staff", name=f"S {index:02}")
        path = reverse("admin:report_email_new")
        addresses = [f"address:a{index}@example.org" for index in range(26)]
        twice = "A recipient is chosen twice. Remove one."
        for recipients, message in (
            (["address:not an address"], "is not a valid email address."),
            (["address:a@example.org", "address:A@Example.org"], twice),
            (["group:staff", "group:staff"], twice),
            (
                [
                    "address:STAFF@example.org",
                    f"user:{user_id(store, 'staff@example.org')}",
                ],
                twice,
            ),
            ([f"user:{uuid4()}"], "is not a current Administrator or Staff user."),
            (
                ["address:Lee@Example.org"],
                "lee@example.org is a Ministry leader; Ministry leaders don",
            ),
            (addresses, "Choose at most 25 outside addresses."),
            (
                addresses[:25]
                + ["group:staff", "group:administrators"]
                + [
                    f"user:{user_id(store, f's{index}@example.org')}"
                    for index in range(24)
                ],
                "Choose at most 50 recipients.",
            ),
        ):
            refused_at(
                post(admin, path, fields(recipient=recipients)), "recipient", message
            )
        assert "Staff requests" not in reports()
        answer = post(admin, path, fields(recipient=["address:STAFF@Example.org"]))
        assert answer.status_code == 200, answer.content
        assert confirm(admin, path, answer).status_code == 302
        apply_requests(store)
        assert reports()["Staff requests"]["values"]["recipients"] == [
            {"user": user_id(store, "staff@example.org")}
        ]


def test_a_demoted_or_removed_user_is_refused_on_edit(live_response_service, google):
    """A saved user who is no longer Administrator or Staff is refused at
    Recipients when Edit is reviewed, until the chip is removed."""
    from .report_builders import revoke_admin
    from .user_builders import user_id

    harness = live_response_service
    store = harness.service.store
    with campaign_clock(INSTANT):
        admin = started(harness)
        set_user(store, "staff@example.org", "staff", name="Pat Doe")
        set_user(store, "other@example.org", "staff", name="Other")
        staff = user_id(store, "staff@example.org")
        other = user_id(store, "other@example.org")
        identifier = add_report(
            store,
            harness.campaign,
            name="Staff weekly",
            recipients=[{"user": staff}, {"user": other}, {"group": "staff"}],
        )
        path = reverse("admin:report_email_edit", args=[identifier])
        set_user(store, "staff@example.org", "ministry_leader")
        revoke_admin(harness, "other@example.org")
        saved = report_values(identifier)
        edit = fields(
            name=saved["name"],
            recipient=[f"user:{staff}", f"user:{other}", "group:staff"],
            sends="repeat",
            editor_mode="advanced",
            frequency=saved["schedule"]["frequency"],
            weekdays=[str(day) for day in saved["schedule"].get("weekdays", [])],
            from_kind=saved["schedule"]["from"],
            until_kind=saved["schedule"]["until"],
            time="10:30",
        )
        message = "is not a current Administrator or Staff user."
        refused_at(post(admin, path, edit), "recipient", message)
        refused_at(
            post(admin, path, edit | {"recipient": [f"user:{other}", "group:staff"]}),
            "recipient",
            message,
        )
        answer = post(admin, path, edit | {"recipient": ["group:staff"]})
        assert answer.status_code == 200, answer.content


def test_every_refusal_is_shown_in_one_round(live_response_service, google):
    """A taken name is refused with a missing recipient and block, in the
    same answer (#1964); Days shown is refused at its own field."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        add_digest(harness.service.store, harness.campaign, weekly=True)
        admin = started(harness)
        path = reverse("admin:report_email_new")
        name = next(iter(reports()))
        answer = post(admin, path, fields(name=name.upper(), recipient=[], block=[]))
        refused_at(
            answer, "name", "Another report email of this campaign has this name."
        )
        refused_at(answer, "recipient", "Choose at least one recipient.")
        refused_at(answer, "block", "Add at least one block.")
        answer = post(admin, path, fields(block=["day_by_day"], days_shown="99"))
        refused_at(answer, "days_shown", "14")
        text = answer.content.decode()
        field = text[
            text.index('id="report-days_shown"') - 300 : text.index(
                'id="report-days_shown"'
            )
            + 400
        ]
        assert 'aria-invalid="true"' in field


def test_staff_may_not_post_to_the_editor(live_response_service, google):
    """Every post of New, Edit and the live preview is refused for Staff
    (403), and nothing changes."""
    from .report_builders import users_form

    harness = live_response_service
    store = harness.service.store
    with campaign_clock(INSTANT):
        users_form(store)
        identifier = add_digest(store, harness.campaign, weekly=True)
        set_user(store, "staff@example.org", "staff")
        google[0]["email"] = "staff@example.org"
        staff, _ = signed_in()
        new = reverse("admin:report_email_new")
        edit = reverse("admin:report_email_edit", args=[identifier])
        before = SystemConfiguration.objects.get().active_configuration.digest
        for path, data in (
            (new, fields()),
            (new, {"action": "confirm", "preview": "x"}),
            (edit, fields()),
            (edit, {"action": "skip_preview", "send": "2054-11-04"}),
            (edit, {"action": "unskip_preview", "send": "2054-11-04"}),
            (edit, {"action": "delete_preview"}),
            (edit, {"action": "confirm", "preview": "x"}),
            (reverse("admin:report_email_schedule_preview"), fields()),
        ):
            answer = post(staff, path, data)
            assert answer.status_code == 403, (path, data, answer.status_code)
        assert SystemConfiguration.objects.get().active_configuration.digest == before


def stale_apply(browser, path, answer):
    """Apply ``answer``, which must be refused as stale (409); its message.

    A changed configuration and changed report work both make the signed
    preview out of date; a passed send says so in its own words.
    """
    refused = confirm(browser, path, answer)
    assert refused.status_code == 409, refused.content
    body = refused.json()
    assert [error["code"] for error in body["errors"]] == ["stale_version"]
    return body["refusal"]["message"]


def test_apply_is_refused_after_a_stale_or_raced_review(
    live_response_service, google, monkeypatch
):
    """A review on an old version is refused (409); Apply is refused once the
    configuration or the campaign's report work changed after Review, or
    once a reviewed one-time send's time has passed."""

    from parishkit.stewardship.accounts import admin_editing

    from .report_builders import produce

    harness = live_response_service
    store = harness.service.store
    with campaign_clock(INSTANT):
        admin = started(harness)
        path = reverse("admin:report_email_new")
        stale = post(admin, path, fields() | {"base_digest": "0" * 64})
        assert stale.status_code == 409, stale.content
        # The configuration changes between Review and Apply.
        answer = post(admin, path, fields())
        assert answer.status_code == 200, answer.content
        identifier = add_digest(store, harness.campaign, weekly=True)
        assert stale_apply(admin, path, answer) == "This preview is out of date."
        # The report work changes between Review and Apply.
        answer = post(admin, path, fields())
        assert answer.status_code == 200, answer.content
        assert produce()
        assert stale_apply(admin, path, answer) == "This preview is out of date."
        # The reviewed one-time send passes before Apply (#1464).
        answer = post(admin, path, fields(once_date="2054-10-29", time="9:00"))
        assert answer.status_code == 200, answer.content
        late = datetime(2054, 10, 29, 13, 5, tzinfo=UTC) + timedelta(minutes=1)
        monkeypatch.setattr(admin_editing, "database_now", lambda: late)
        assert stale_apply(admin, path, answer).startswith(
            "A send time in this change has passed since it was reviewed"
        )
        assert "Staff requests" not in reports()
        assert report_values(identifier) is not None


def test_skip_refusals_from_edit(live_response_service, google, monkeypatch):
    """Each send a skip or unskip names is checked on its own: a past send
    is refused (403), a send skipped already (for an unskip, one not
    skipped) 409, a day that is no send of the report or a send named twice
    400, and one refused send refuses the whole change. Apply is refused
    once any reviewed send's time has passed (#1464)."""
    from parishkit.stewardship.accounts import admin_editing
    from parishkit.stewardship.reports import report_email_changes
    from parishkit.stewardship.reports.report_email_changes import send_at

    # The review reads the database's clock; the campaign's is INSTANT.
    monkeypatch.setattr(report_email_changes, "database_now", lambda: INSTANT)

    harness = live_response_service
    store = harness.service.store
    with campaign_clock(INSTANT):
        # A daily report: several sends are still ahead.
        identifier = add_digest(store, harness.campaign)
        admin = started(harness)
        path = reverse("admin:report_email_edit", args=[identifier])
        values = report_values(identifier)
        campaign = harness.campaign
        campaign.refresh_from_db()
        window = campaign.active_configuration.values
        days = [
            (date(2054, 10, 1) + timedelta(days=offset)).isoformat()
            for offset in range(40)
        ]
        sends = {day: send_at(values, window, day) for day in days}
        past = next(
            day for day, send in sends.items() if send and send.due_at <= INSTANT
        )
        none = next(
            day for day, send in sends.items() if send is None and day > "2054-10-29"
        )
        ahead, later = [
            day for day, send in sends.items() if send and send.due_at > INSTANT
        ][:2]
        before = SystemConfiguration.objects.get().active_configuration.digest
        for data, status in (
            ({"action": "skip_preview", "send": past}, 403),
            ({"action": "skip_preview", "send": [later, past]}, 403),
            ({"action": "skip_preview", "send": none}, 400),
            ({"action": "skip_preview", "send": [ahead, ahead]}, 400),
            ({"action": "skip_preview"}, 400),
            ({"action": "unskip_preview", "send": ahead}, 409),
        ):
            answer = post(admin, path, data)
            assert answer.status_code == status, (data, answer.content)
        assert SystemConfiguration.objects.get().active_configuration.digest == before
        answer = post(admin, path, {"action": "skip_preview", "send": ahead})
        assert confirm(admin, path, answer).status_code == 302
        apply_requests(store)
        answer = post(admin, path, {"action": "skip_preview", "send": [later, ahead]})
        assert answer.status_code == 409, answer.content
        answer = post(admin, path, {"action": "unskip_preview", "send": [ahead, later]})
        assert answer.status_code == 409, answer.content
        # The reviewed unskip's send passes before Apply: refused, the skip
        # kept.
        answer = post(admin, path, {"action": "unskip_preview", "send": ahead})
        assert answer.status_code == 200, answer.content
        passed = sends[ahead].due_at + timedelta(minutes=1)
        monkeypatch.setattr(admin_editing, "database_now", lambda: passed)
        assert stale_apply(admin, path, answer).startswith(
            "A send time in this change has passed since it was reviewed"
        )
        assert ahead in report_values(identifier)["skip_dates"]
        # A skipped send that has passed is never sent again.
        monkeypatch.setattr(report_email_changes, "database_now", lambda: passed)
        answer = post(admin, path, {"action": "unskip_preview", "send": ahead})
        assert answer.status_code == 403, answer.content


def test_the_calendar_month_is_accepted_and_cancel_returns_to_it(
    live_response_service, google
):
    """The Campaign calendar's link adds the month it shows (#1963): Edit
    opens (200) and Cancel returns to that month; any other value is 400."""
    harness = live_response_service
    with campaign_clock(INSTANT):
        identifier = add_digest(harness.service.store, harness.campaign, weekly=True)
        admin = started(harness)
        path = reverse("admin:report_email_edit", args=[identifier])
        slot = page_slots(admin, path)[0]
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            page = admin.get(f"{path}?send={slot}&calendar=2054-10")
            bad = admin.get(f"{path}?send={slot}&calendar=October")
            head = admin.head(f"{path}?send={slot}&calendar=October")
        assert page.status_code == 200
        calendar = reverse("admin:schedule_calendar")
        assert f'<a href="{calendar}?month=2054-10">Cancel</a>' in page.content.decode()
        assert bad.status_code == 400 and head.status_code == 400
