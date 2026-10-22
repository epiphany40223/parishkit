"""The campaign work status Campaign settings and Home follow (#1619).

Real Google sessions, a real campaign work gate and the real views: while
campaign work holds changes, Campaign settings draws its pending status
region and Home its pending first-campaign panel; the passive status they
poll says when the hold ends, never extends the sign-in and records no
audited view; and Campaign settings' own re-read, sent passively, does not
extend it either.
"""

from uuid import uuid4

import pytest
from django.test import Client

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate

from ..test_source_corpus import source
from .auth_builders import signed_in, unguarded
from .campaign_builders import add_draft
from .test_admin_reachability_postgresql import _sign_in
from .test_campaign_views_postgresql import fields, post, url
from .test_debug_production_banner_postgresql import force_mode
from .test_parish_views_postgresql import digest
from .test_source_families_postgresql import (  # noqa: F401
    publish,
    source_singletons,
)

pytestmark = pytest.mark.django_db(transaction=True)
STATUS = "/admin/campaign/work-status/"
SETTINGS = STATUS + "?view=settings"
HOME = STATUS + "?view=home"
LOCKED_NOW = "Some settings you changed can no longer change now."


def hold(row):
    """Hold campaign changes with a running work gate for ``row``."""
    with unguarded():
        return CampaignWorkGate.objects.create(
            campaign_id=row.pk,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state="running",
        )


def release(gate):
    """End the hold ``gate`` set."""
    with unguarded():
        gate.delete()


def activity():
    """The sign-in's last activity: a renewal moves it."""
    return PortalSession.objects.get(revoked_at__isnull=True).last_activity_at


def test_settings_follow_the_hold_passively(auth_service, google):
    """While work holds the draft, Campaign settings draws its settings
    locked, with a reason that does not say to reload, and the pending
    status region. The status is pending, never cached; once the hold ends
    it settles with the one follow-up re-read of the form's region, and the
    page drawn then has no region. Neither the status reads nor the page's
    passive re-read renew the sign-in or record an audited view; the
    reader's own load of the page does renew it."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    gate = hold(row)
    page = browser.get(url(row)).content.decode()
    assert 'data-live-status="campaign-work"' in page
    assert f'data-live-url="{SETTINGS}"' in page
    assert "Locked while background work or going live is under way." in page
    assert "Reload this page" not in page
    events = AuditEvent.objects.count()
    before = activity()
    response = browser.get(SETTINGS)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert b"data-live-pending" in response.content
    assert b"data-reread" not in response.content
    passive = browser.get(url(row), HTTP_X_PARISHKIT_READ="passive")
    assert passive.status_code == 200
    assert b"data-live-pending" in passive.content
    release(gate)
    response = browser.get(SETTINGS)
    assert response.status_code == 200
    assert b"data-live-pending" not in response.content
    assert b'data-reread href="#settings-editor"' in response.content
    freed = browser.get(url(row), HTTP_X_PARISHKIT_READ="passive")
    assert freed.status_code == 200
    assert b'data-live-status="campaign-work"' not in freed.content
    assert b"Locked while background work" not in freed.content
    assert activity() == before
    assert AuditEvent.objects.count() == events
    assert browser.get(url(row)).status_code == 200
    assert activity() > before


def test_a_review_posted_from_before_the_hold_says_to_reload(auth_service, google):
    """A page drawn before the hold does not follow it, so a Review it posts
    once work holds the draft is refused (409) with the held reason and to
    reload, in the dialog; nothing is recorded."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    row = Campaign.objects.get()
    browser, _ = signed_in()
    page = browser.get(url(row))
    assert b'data-live-status="campaign-work"' not in page.content
    data = fields(store, row, name="Changed during the hold")
    gate = hold(row)
    try:
        refused = post(browser, url(row), data | {"base_digest": digest(page)})
    finally:
        release(gate)
    assert refused.status_code == 409
    refusal = refused.json()["refusal"]
    assert refusal["message"] == LOCKED_NOW
    assert refusal["fix"].startswith("Locked while background work")
    assert refusal["fix"].endswith("Reload this page to start again.")


def test_home_follows_the_hold_through_the_status(auth_service, google, monkeypatch):
    """With no campaign yet, Home draws Create the campaign unavailable while
    campaign work holds it, in a pending panel; the status answers with the
    panel itself, pending while held and offering Create the campaign once
    the hold ends. The hold is forced, because a real one needs a campaign
    row (test_campaign_create_postgresql). Status reads record no view."""
    from parishkit.stewardship.campaigns import single_campaign

    publish(source())
    browser, _ = signed_in()
    monkeypatch.setattr(single_campaign, "first_campaign_held", lambda: True)
    home = browser.get("/admin/").content.decode()
    assert 'data-live-status="first-campaign"' in home
    assert "data-live-pending" in home
    assert "Reload this page" not in home
    events = AuditEvent.objects.count()
    before = activity()
    held = browser.get(HOME)
    assert held.status_code == 200
    assert held["Cache-Control"] == "no-store"
    assert b"data-live-pending" in held.content
    assert b'aria-describedby="create-campaign-reason"' in held.content
    monkeypatch.setattr(single_campaign, "first_campaign_held", lambda: False)
    ready = browser.get(HOME)
    assert ready.status_code == 200
    assert b"data-live-pending" not in ready.content
    assert b'href="/admin/campaign/first/"' in ready.content
    assert AuditEvent.objects.count() == events
    assert activity() == before


def test_home_status_says_created_only_with_a_campaign(auth_service, google):
    """Once Home can no longer offer Create the campaign, the status says a
    campaign has been created only while a campaign row exists: switched to
    Production mode with none, it answers Home's no-campaign sentence."""
    publish(source())
    browser, _ = signed_in()
    force_mode("production")
    none = browser.get(HOME)
    assert none.status_code == 200
    assert b"Campaign created" not in none.content
    assert b"There is no current campaign." in none.content
    # A campaign is created only in Testing mode, so go back to add one.
    force_mode("testing")
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    created = browser.get(HOME)
    assert created.status_code == 200
    assert b"Campaign created" in created.content
    assert b"There is no current campaign." not in created.content


def test_status_refuses_a_reader_without_configure(auth_service, google):
    """A signed-in Staff user, who may not configure, is refused (403) for
    either view, so the status shows nothing beyond Configure holders."""
    publish(source())
    browser = _sign_in(auth_service.store, google, "staff")
    assert browser.get("/admin/").status_code == 200
    assert browser.get(SETTINGS).status_code == 403
    assert browser.get(HOME).status_code == 403


def test_status_refuses_bad_views_and_signed_out_callers(auth_service, google):
    """Only ``view`` (settings or home) is accepted, once; a caller with no
    sign-in is refused before the options are looked at."""
    publish(source())
    browser, _ = signed_in()
    for query in ("", "?view=other", "?view=home&view=home", "?view=home&x=1"):
        assert browser.get(STATUS + query).status_code == 400, query
    assert post(browser, SETTINGS, {}).status_code == 405
    assert Client().get(SETTINGS).status_code == 403
    assert Client().get(STATUS + "?view=other").status_code == 403
