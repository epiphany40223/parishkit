"""A campaign wizard step saves directly, with no review dialog (#1404 PR 4).

Real Google sessions, the restricted web role and the installer: inside the
campaign wizard, Campaign settings and Share options post ``save`` with the
page's request key. The server checks the form as a Review does and records
the same configuration request at once; a repeat of the same save returns
that change's status, a different change from the same page is a save of
its own (refused here as stale), a stale version is refused, a draft's
dates-only change with schedules leads to Dates and mail schedules'
combined review with nothing recorded, and once the wizard no longer
applies the pages draw their ordinary Save and review dialog again and a
direct save is refused with nothing recorded.
"""

import re
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.http import QueryDict
from django.urls import reverse

from parishkit.stewardship.accounts import campaign_wizard
from parishkit.stewardship.accounts.admin_editing import direct_key
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.models import Campaign

from ..test_share_forms import data_for
from .auth_builders import signed_in, unguarded
from .campaign_builders import add_draft
from .test_campaign_setup_review_postgresql import web
from .test_campaign_stepper_postgresql import status_of, stepper
from .test_campaign_views_postgresql import apply, fields, post, requested, url
from .test_schedule_views_postgresql import setup as draft_with_reminder
from .test_share_views_postgresql import setup

pytestmark = pytest.mark.django_db(transaction=True)


def drawn_key(body):
    """The request key a direct-save page drew into its form."""
    return UUID(re.search(r'name="request_key" value="([^"]+)"', body).group(1))


def assert_direct(body):
    """The page as the wizard draws it: no Save of its own, the row saves."""
    assert "data-direct-save" in body
    assert '<input type="hidden" name="action" value="save">' in body
    assert "data-review-open" not in body and ">Save</button>" not in body
    assert re.search(
        r'<button type="submit" form="settings-form" [^>]*data-campaign-save'
        r"[^>]*>Save and continue</button>",
        body,
    )
    assert 'id="campaign-step-status" role="status"' in body


def test_share_options_save_directly_and_complete_the_step(auth_service, google):
    """Save and continue records the change at once; a repeat is the same one."""
    store = auth_service.store
    row, previous, path = setup(store)
    browser, _ = signed_in()
    with web():
        body = browser.get(path).content.decode()
    assert_direct(body)
    assert f'data-next-url="{reverse("admin:content_catalog")}"' in body
    key = drawn_key(body)
    data = data_for(previous) | {
        "action": "save",
        "request_key": str(key),
        "base_digest": store.active().digest,
        "options-0-label": "Saved directly",
    }
    requests = ConfigurationChangeRequest.objects.count()
    with web():
        response = post(browser, path, data)
    # Answered with the change's status, which the page follows.
    status_url = reverse("admin:configuration_request", args=[requested(response)])
    assert response["Location"] == status_url
    recorded = ConfigurationChangeRequest.objects.get(pk=requested(response))
    # The change's key comes from the page's key and the values posted.
    assert recorded.request_key == direct_key(QueryDict(urlencode(data, doseq=True)))
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    apply(store, response)
    row.refresh_from_db()
    assert row.active_configuration.values["share_options"][0]["label"] == (
        "Saved directly"
    )
    with web():
        assert b"Applied: your change is saved" in browser.get(status_url).content
        region = stepper(browser.get(path).content)
    assert status_of(region, "Share options") == "Current step — Completed"
    # A repeat of the same page's save (its answer lost), now that its
    # version is out of date, is answered with the same change.
    with web():
        again = post(browser, path, data)
    assert again.status_code == 302 and again["Location"] == status_url
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    # A different change from that same page (its key) is never answered
    # with the first change: it is checked as a save of its own, and the
    # version it was drawn at is no longer the current one.
    different = data | {"options-0-label": "A later change"}
    with web():
        refused = post(browser, path, different)
    assert refused.status_code == 409
    assert refused.stewardship_reason == "stale_page"
    assert "This page changed in another tab or session." in refused.content.decode()
    assert ConfigurationChangeRequest.objects.count() == requests + 1
    # A save from a page drawn before that change, with a new key, is stale.
    stale = data | {"request_key": str(uuid4()), "options-0-label": "Older page"}
    with web():
        refused = post(browser, path, stale)
    assert refused.status_code == 409
    assert ConfigurationChangeRequest.objects.count() == requests + 1


def test_campaign_settings_save_directly_until_setup_ends(
    auth_service, google, monkeypatch
):
    """Campaign settings saves directly in the wizard, and only there."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    with web():
        body = browser.get(url(row)).content.decode()
    assert_direct(body)
    key = drawn_key(body)
    data = fields(store, row, name="Renamed directly") | {
        "action": "save",
        "request_key": str(key),
    }
    with web():
        response = post(browser, url(row), data)
    assert response["Location"] == reverse(
        "admin:configuration_request", args=[requested(response)]
    )
    apply(store, response)
    row.refresh_from_db()
    assert row.active_configuration.name == "Renamed directly"
    # Nothing changed: the save is refused, as an unchanged Review is.
    unchanged = fields(store, row) | {"action": "save", "request_key": str(uuid4())}
    with web():
        assert post(browser, url(row), unchanged).status_code == 400
    # Setup ends between the page's own checks and the work lock (a go-live
    # starts meanwhile): the save is refused under the lock, nothing is
    # recorded, and the refusal says so in plain words.
    requests = ConfigurationChangeRequest.objects.count()
    late = fields(store, row, name="Too late") | {
        "action": "save",
        "request_key": str(uuid4()),
    }
    monkeypatch.setattr(campaign_wizard, "current_wizard", lambda *_, **__: None)
    with web():
        refused = post(browser, url(row), late)
    monkeypatch.undo()
    assert refused.status_code == 409
    assert "Campaign setup has ended, so nothing was saved." in refused.content.decode()
    assert ConfigurationChangeRequest.objects.count() == requests
    # Once a go-live has started (the credential row's gate, seeded as only
    # a superuser could), the page has its own Save and review dialog again.
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_campaign_credentials (id, correlation_id,"
            " version, campaign_id, go_live_gate, eligibility_digest,"
            " eligible_count, population_dirty)"
            " VALUES (%s, %s, 1, %s, true, '', 0, true)",
            [uuid4(), uuid4(), row.pk],
        )
    with web():
        body = browser.get(url(row)).content.decode()
        locked = post(browser, url(row), late | {"request_key": str(uuid4())})
    assert "data-direct-save" not in body and "data-campaign-save" not in body
    assert 'data-review-open="campaign-review"' in body
    assert locked.status_code == 409
    assert ConfigurationChangeRequest.objects.count() == requests


def test_a_drafts_dates_alone_with_schedules_lead_to_the_combined_review(
    auth_service, google
):
    """Save and continue with only the dates changed opens the combined review.

    With mail schedules, a draft's dates-only change is Dates and mail
    schedules' to review (every mailing reconciled with the new dates), so
    the direct save answers with the redirect there, carrying the new
    dates, and records nothing.
    """
    store = auth_service.store
    row, schedules = draft_with_reminder(store)
    browser, _ = signed_in()
    with web():
        body = browser.get(url(row)).content.decode()
    assert_direct(body)
    requests = ConfigurationChangeRequest.objects.count()
    data = fields(store, row, end_date="2054-11-15") | {
        "action": "save",
        "request_key": str(drawn_key(body)),
    }
    with web():
        response = post(browser, url(row), data)
    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert location.path == reverse("admin:schedule_settings") == schedules
    assert parse_qs(location.query)["end_date"] == ["2054-11-15"]
    assert ConfigurationChangeRequest.objects.count() == requests
