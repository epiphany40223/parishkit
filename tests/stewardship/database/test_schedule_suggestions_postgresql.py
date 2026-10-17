"""Dates and mail schedules' suggested schedules (#1404 PR 6a).

Real Google sessions, the restricted web role and the installer: inside the
campaign wizard, a draft with no schedule saved lists the suggested starting
schedules, "Not saved yet", and its action row's Add these schedules and
continue posts only the page's version and request key. The server adds the
same set in one configuration change at once; a repeat returns that change's
status, a page drawn before it is refused, and once the wizard no longer
applies nothing is suggested and a late save records nothing.
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
from .test_campaign_setup_review_postgresql import web
from .test_campaign_stepper_postgresql import status_of, stepper
from .test_campaign_views_postgresql import apply, post, requested

pytestmark = pytest.mark.django_db(transaction=True)

LIST = "/admin/campaign/schedules/"
SUGGESTED = "/admin/campaign/schedules/suggestion/"


def setup(store, kinds=("initial", "reminder", "weekly_digest"), **dates):
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
    assert kinds == ["initial", "reminder", "weekly_digest"]
    assert body.count('<span class="status-badge">Not saved yet</span>') == 3
    assert "Every Monday" in body
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
        "weekly_digest": (None, "08:00:00", 0),
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
