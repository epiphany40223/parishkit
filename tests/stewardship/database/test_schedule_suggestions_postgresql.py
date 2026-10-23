"""Dates and mail schedules' suggested schedules (#1404 PR 6a).

Real Google sessions, the restricted web role and the installer: inside the
campaign wizard, a draft with no schedule saved lists the suggested starting
schedules, "Not saved yet", and its action row's Add these schedules and
continue posts only the page's version and request key. The server adds the
same set in one configuration change at once; a repeat returns that change's
status, a page drawn before it is refused, and once the wizard no longer
applies nothing is suggested and a late save records nothing.

The same panel offers the report emails' Daily and Weekly starting points
(#2038): each ticked one is added in that one change, so the Family
schedules and the reports apply in one configuration version; a ticked
report without a time is refused at its time field with nothing recorded.
"""

import re
from html import unescape
from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest
from django.http import QueryDict
from django.urls import reverse

from parishkit.stewardship.accounts import campaign_wizard, schedule_entry_views
from parishkit.stewardship.accounts.admin_editing import direct_key
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.models import ScheduleDefinition

from ..campaign_factory import campaign as campaign_record
from ..content_factory import content
from .auth_builders import signed_in
from .campaign_builders import change
from .report_builders import users_form
from .test_campaign_setup_review_postgresql import web
from .test_campaign_stepper_postgresql import status_of, stepper
from .test_campaign_views_postgresql import apply, post, requested

pytestmark = pytest.mark.django_db(transaction=True)

LIST = "/admin/campaign/schedules/"
SUGGESTED = "/admin/campaign/schedules/suggestion/"


def setup(store, kinds=("initial", "reminder"), **dates):
    """A draft with one saved email of each mail type in ``kinds`` and no schedule.

    Returns the emails by mail type.
    """
    owner = campaign_record(**dates)
    emails = {
        kind: content(owner["id"], kind="email", slot=kind, subject=f"{kind} mail")
        for kind in kinds
    }
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {"operation": "add", "section": "campaigns", **owner},
            *(
                {"operation": "add", "section": "content", **row}
                for row in emails.values()
            ),
        ],
    )
    assert result.state == "applied"
    return emails


def drawn(body):
    """The suggested schedules form's posted fields, as the page drew them."""
    form = re.search(
        r'<form method="post" action="[^"]+" id="suggested-schedules-form"'
        r"[^>]*>(.*?)</form>",
        body,
        re.S,
    ).group(1)
    return {
        name: value
        for name, value in re.findall(
            r'name="(base_digest|request_key)" value="([^"]+)"', form
        )
    }


def test_the_suggested_schedules_are_listed_and_added(auth_service, google):
    """Add these schedules and continue records the listed set at once."""
    store = auth_service.store
    emails = setup(store)
    browser, _ = signed_in()
    with web():
        body = unescape(browser.get(LIST).content.decode())
    assert '<h2 id="suggested-schedules-title">Suggested schedules</h2>' in body
    kinds = re.findall(
        r'data-schedule-state="suggested" data-suggested-kind="(\w+)"', body
    )
    assert kinds == ["initial", "reminder"]
    assert body.count('<span class="status-badge">Not saved yet</span>') == 2
    # Report emails replaced the digests: no digest is a suggested
    # schedule (#1786). Report emails exist only in the users form, which
    # this fixture's sign-in rules are not in, so none is offered (#2038).
    assert "data-suggested-report" not in body
    assert "data-require-complete" not in body.split('id="suggested-schedules-form"')[1]
    assert 'data-direct-saved="Suggested schedules added."' in body
    assert re.search(
        r'<button type="submit" form="suggested-schedules-form" [^>]*'
        r"data-campaign-add[^>]*>Add these schedules and continue</button>",
        body,
    )
    assert "Continue without schedules for now" in body
    assert f'data-next-url="{reverse("admin:artwork_settings")}"' in body
    data = drawn(body)
    assert data["base_digest"] == store.active().digest
    UUID(data["request_key"])
    requests = ConfigurationChangeRequest.objects.count()
    with web():
        response = post(browser, SUGGESTED, data)
    status_url = reverse("admin:configuration_request", args=[requested(response)])
    assert response.status_code == 302 and response["Location"] == status_url
    recorded = ConfigurationChangeRequest.objects.get(pk=requested(response))
    assert recorded.request_key == direct_key(QueryDict(urlencode(data)))
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    apply(store, response)
    # Nothing ticked (the fields left unposted): no report email is added.
    assert not ScheduleDefinition.objects.filter(kind="report").exists()
    saved = {
        row.kind: row.current_revision.values
        for row in ScheduleDefinition.objects.filter(current_revision__isnull=False)
    }
    assert {
        kind: (values["date"], values["time"], values["weekday"])
        for kind, values in saved.items()
    } == {
        "initial": ("2054-10-01", "09:00:00", None),
        "reminder": ("2054-10-24", "09:00:00", None),
    }
    assert {kind: values["template_version"] for kind, values in saved.items()} == {
        kind: row["id"] for kind, row in emails.items()
    }
    # A repeat (its answer lost) is answered with the same change.
    with web():
        again = post(browser, SUGGESTED, data)
    assert again.status_code == 302 and again["Location"] == status_url
    # The step is completed, and the page no longer suggests anything.
    with web():
        page = unescape(browser.get(LIST).content.decode())
    assert status_of(stepper(page.encode()), "Dates and mail schedules") == (
        "Current step — Completed"
    )
    assert "Suggested schedules" not in page and "data-campaign-add" not in page
    assert "Continue to Campaign images" in page
    # A save of its own from a page drawn before the schedules were added
    # is refused, nothing recorded.
    with web():
        late = post(browser, SUGGESTED, data | {"request_key": str(uuid4())})
    assert late.status_code == 409
    assert late.stewardship_reason == "stale_page"
    # The specific answer, not the generic stale-page one, even though the
    # schedules saved meanwhile also made the page's version stale.
    assert "The suggested schedules no longer apply, so nothing was saved." in (
        late.content.decode()
    )
    with web():
        fresh = post(
            browser,
            SUGGESTED,
            {"base_digest": store.active().digest, "request_key": str(uuid4())},
        )
    assert fresh.status_code == 409
    assert "The suggested schedules no longer apply, so nothing was saved." in (
        fresh.content.decode()
    )
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    # Only the version and the key are accepted, and only by POST.
    with web():
        extra = post(browser, SUGGESTED, data | {"schedule-kind": "initial"})
        bad = post(browser, SUGGESTED, data | {"request_key": "not-a-key"})
        assert browser.get(SUGGESTED).status_code == 405
    assert extra.status_code == 400 and bad.status_code == 400


def test_without_an_invitation_email_nothing_is_suggested(auth_service, google):
    """No Initial invitation email: the step keeps its plain Continue."""
    setup(auth_service.store, kinds=("reminder",))
    browser, _ = signed_in()
    with web():
        body = unescape(browser.get(LIST).content.decode())
    assert "Suggested schedules" not in body and "data-campaign-add" not in body
    assert "Continue without schedules for now" in body
    assert "Choose New scheduled email to add the Initial invitation first." in body


def test_a_passed_initial_time_suggests_nothing_and_refuses_the_save(
    auth_service, google
):
    """A start date already past would send at once, so nothing is offered.

    The page keeps its plain Continue (no panel, no Add), and a save from a
    page drawn earlier, before the time passed, is refused with nothing
    recorded.
    """
    store = auth_service.store
    setup(store, start_date="2020-01-01", end_date="2020-03-01")
    browser, _ = signed_in()
    with web():
        body = unescape(browser.get(LIST).content.decode())
    assert "Suggested schedules" not in body and "data-campaign-add" not in body
    assert "Continue without schedules for now" in body
    requests = ConfigurationChangeRequest.objects.count()
    with web():
        late = post(
            browser,
            SUGGESTED,
            {"base_digest": store.active().digest, "request_key": str(uuid4())},
        )
    assert late.status_code == 409
    assert "The suggested schedules no longer apply, so nothing was saved." in (
        late.content.decode()
    )
    assert ConfigurationChangeRequest.objects.count() == requests


def test_a_page_drawn_before_another_change_is_stale(auth_service, google):
    """A change that leaves the set as it was still makes the page stale.

    Another saved revision (here a page's) moves the configuration's version
    while still no schedule is saved and the suggested set is unchanged, so
    the save meets the page-version check: the generic stale-page refusal,
    with nothing recorded.
    """
    store = auth_service.store
    emails = setup(store)
    browser, _ = signed_in()
    with web():
        data = drawn(unescape(browser.get(LIST).content.decode()))
    owner = emails["initial"]["values"]["campaign_id"]
    result = change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "content", **content(owner)}],
    )
    assert result.state == "applied"
    requests = ConfigurationChangeRequest.objects.count()
    with web():
        refused = post(browser, SUGGESTED, data)
    assert refused.status_code == 409
    assert refused.stewardship_reason == "stale_page"
    assert "This page changed in another tab or session." in refused.content.decode()
    assert ConfigurationChangeRequest.objects.count() == requests


def test_outside_the_wizard_nothing_is_suggested(auth_service, google, monkeypatch):
    """Once the wizard no longer applies, the page and the save refuse it.

    A started go-live (or a campaign no longer a Testing draft) ends it;
    here ``shown`` says so, as it would then, for the page and under the
    work lock alike.
    """
    store = auth_service.store
    setup(store)
    browser, _ = signed_in()
    with web():
        data = drawn(unescape(browser.get(LIST).content.decode()))
    monkeypatch.setattr(campaign_wizard, "shown", lambda *_, **__: False)
    requests = ConfigurationChangeRequest.objects.count()
    with web():
        page = browser.get(LIST).content.decode()
        late = post(browser, SUGGESTED, data)
    assert "Suggested schedules" not in page and "data-direct-save" not in page
    assert late.status_code == 409
    assert "Campaign setup has ended, so nothing was saved." in late.content.decode()
    assert ConfigurationChangeRequest.objects.count() == requests


def test_send_work_that_blocks_the_change_refuses_it(auth_service, google, monkeypatch):
    """With sends blocking the change, the review has no preview to save."""
    store = auth_service.store
    setup(store)
    browser, _ = signed_in()
    with web():
        data = drawn(unescape(browser.get(LIST).content.decode()))
    original = schedule_entry_views.build_preview

    def blocked(*args, **kwargs):
        """The real review, as if two sends blocked it."""
        context = original(*args, **kwargs)
        return context and context | {"preview": None, "blocking": 2}

    monkeypatch.setattr(schedule_entry_views, "build_preview", blocked)
    requests = ConfigurationChangeRequest.objects.count()
    with web():
        refused = post(browser, SUGGESTED, data)
    assert refused.status_code == 409
    assert refused.stewardship_reason == "send_work_blocks"
    assert ConfigurationChangeRequest.objects.count() == requests


def ticks(**times):
    """The suggested report emails' posted fields: each named key ticked
    with its typed time, in the parish's own zone."""
    fields = {"zone": "America/New_York"}
    for key, typed in times.items():
        fields |= {f"report_{key}": "yes", f"report_{key}_time": typed}
    return fields


def test_ticked_report_emails_are_added_with_the_schedules(auth_service, google):
    """Both reports and both Family schedules apply in one configuration version."""
    store = auth_service.store
    setup(store)
    users_form(store)
    browser, _ = signed_in()
    with web():
        body = unescape(browser.get(LIST).content.decode())
    assert '<h3 id="suggested-reports-title">Suggested report emails</h3>' in body
    data = drawn(body) | ticks(daily="7:00 AM", weekly="8:30 AM")
    requests = ConfigurationChangeRequest.objects.count()
    with web():
        response = post(browser, SUGGESTED, data)
    assert response.status_code == 302
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    apply(store, response)
    rows = list(ScheduleDefinition.objects.filter(current_revision__isnull=False))
    assert sorted(row.kind for row in rows) == [
        "initial",
        "reminder",
        "report",
        "report",
    ]
    # One applied version holds all four: the save is one change.
    assert len({row.current_revision.configuration_id for row in rows}) == 1
    reports = {
        row.current_revision.values["name"]: row.current_revision.values
        for row in rows
        if row.kind == "report"
    }
    assert set(reports) == {"Daily report", "Weekly report"}
    assert reports["Daily report"]["schedule"]["time"] == "07:00:00"
    assert reports["Weekly report"]["schedule"]["time"] == "08:30:00"
    assert reports["Weekly report"]["recipients"] == [{"group": "administrators"}]
    assert reports["Daily report"]["slot_basis"] == "send_date"


def test_one_ticked_report_is_added_alone(auth_service, google):
    """An unticked row adds nothing, even with a time posted."""
    store = auth_service.store
    setup(store)
    users_form(store)
    browser, _ = signed_in()
    with web():
        data = drawn(unescape(browser.get(LIST).content.decode()))
    data |= ticks(weekly="9:15 AM") | {"report_daily_time": "7:00 AM"}
    with web():
        response = post(browser, SUGGESTED, data)
    assert response.status_code == 302
    apply(store, response)
    names = [
        row.current_revision.values["name"]
        for row in ScheduleDefinition.objects.filter(
            kind="report", current_revision__isnull=False
        )
    ]
    assert names == ["Weekly report"]


def test_a_ticked_report_without_a_time_is_refused_at_its_field(auth_service, google):
    """The page is drawn again (400) with the error at the time field.

    Nothing is recorded: neither the Family schedules nor the other,
    complete, report.
    """
    store = auth_service.store
    setup(store)
    users_form(store)
    browser, _ = signed_in()
    with web():
        data = drawn(unescape(browser.get(LIST).content.decode()))
    data |= ticks(weekly="9:15 AM") | {"report_daily": "yes", "report_daily_time": ""}
    requests = ConfigurationChangeRequest.objects.count()
    with web():
        refused = post(browser, SUGGESTED, data)
    assert refused.status_code == 400
    page = unescape(refused.content.decode())

    error = re.search(
        r'<ul class="errorlist" id="suggested-report_daily_time_error">(.*?)</ul>',
        page,
    )
    assert error and "Enter the time it sends, or untick it." in error.group(1)
    assert 'id="suggested-report_daily_time"' in page
    assert re.search(r'id="suggested-report_daily_time"[^>]*aria-invalid="true"', page)
    # What was typed is kept: the Weekly's time and both ticks.
    assert 'value="9:15 AM"' in page
    assert ConfigurationChangeRequest.objects.count() == requests
    assert not ScheduleDefinition.objects.filter(
        current_revision__isnull=False
    ).exists()
    # An unknown field is still refused outright.
    with web():
        extra = post(browser, SUGGESTED, data | {"report_monthly": "yes"})
    assert extra.status_code == 400
    assert ConfigurationChangeRequest.objects.count() == requests


def test_a_report_the_campaign_has_is_not_suggested(auth_service, google):
    """A saved report named like a starting point leaves that one out."""
    from parishkit.stewardship.campaigns.report_presets import DAILY, starting_point

    store = auth_service.store
    emails = setup(store)
    users_form(store)
    owner = emails["initial"]["values"]["campaign_id"]
    report = starting_point(DAILY, owner)
    report["schedule"]["time"] = "07:00:00"
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "schedules",
                "id": str(uuid4()),
                "values": report,
            }
        ],
    )
    assert result.state == "applied"
    browser, _ = signed_in()
    with web():
        body = unescape(browser.get(LIST).content.decode())
    assert re.findall(r'data-suggested-report="(\w+)"', body) == ["weekly"]
    data = drawn(body) | ticks(daily="7:00 AM")
    with web():
        refused = post(browser, SUGGESTED, data)
    # The Daily's fields are no longer the page's: an unknown request.
    assert refused.status_code == 400
