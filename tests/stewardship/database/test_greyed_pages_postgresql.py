"""A greyed-out menu page opened directly says why, in the menu's words (#1128).

One test per kind of reason check (ui-conventions, "Unavailable menu pages"):
no current campaign, an archived campaign, a missing module, a structure
fixed once live, a campaign that already went live, an unconfirmed
Production and Testing mode. Each opens the page as a browser would and
finds the menu entry's own reason, never "This information changed". The
pure guard that every greyable entry is covered is in
``test_admin_navigation``.
"""

from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.accounts import admin_navigation as navigation
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign

from ..policy_factory import address
from ..test_share_forms import data_for
from .auth_builders import signed_in, unguarded
from .campaign_builders import add_draft, change, command
from .test_share_views_postgresql import setup as financial_draft

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = {"HTTP_ACCEPT": "text/html", "HTTP_SEC_FETCH_MODE": "navigate"}
STALE = b"This information changed"


def _opened(browser, name, reason, forward=None):
    """Open menu page ``name`` as a browser and check its reason page."""
    response = browser.get(f"/admin/{_path(name)}", **PAGE)
    body = response.content.decode()
    assert response.status_code == 409, body
    label = navigation.PAGES[name].label
    assert f"{label} isn&#x27;t available right now" in body
    assert f"{reason}." in body
    assert STALE.decode() not in body
    if forward:
        target = navigation.PAGES[forward].label
        url = f"/admin/{_path(forward)}"
        assert f'<a href="{url}">Go to {target}</a>' in body
    else:
        assert ">Go to " not in body
    # The menu marks the page's own entry, greyed, with the same reason.
    assert body.count(str(reason)) >= 2
    return response


def _path(name):
    """A menu page's path below /admin/."""
    from django.urls import reverse

    return reverse(f"admin:{name}").removeprefix("/admin/")


def _staff(store, google):
    """Make the next Google sign-in a Staff member's."""
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("staff@example.org", roles=("staff",)),
            }
        ],
    )
    google[0]["email"] = "staff@example.org"


def test_no_current_campaign(auth_service, google):
    """Campaign pages without a campaign name it; Home is the way on."""
    browser, _ = signed_in()
    _opened(browser, "campaign_settings", navigation.NO_CAMPAIGN)
    _opened(browser, "response_dashboard", navigation.NO_CAMPAIGN)


def test_missing_module_leads_to_campaign_settings(auth_service, google):
    """A census-only draft's Financial pages name the module and lead on."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    browser, _ = signed_in()
    for name in ("financial_report", "share_settings"):
        _opened(browser, name, navigation.FINANCIAL_MISSING, "campaign_settings")
    _opened(
        browser, "ministry_report", navigation.MINISTRY_MISSING, "campaign_settings"
    )
    # A script asking for the page gets the closed JSON code: neither
    # stale_version nor unavailable (an outage to retry).
    data = browser.get(f"/admin/{_path('financial_report')}").json()
    assert [error["code"] for error in data["errors"]] == ["not_available"]
    assert data["refusal"]["message"] == f"{navigation.FINANCIAL_MISSING}."


def test_testing_mode_leads_to_go_live_readiness(auth_service, google):
    """Production-only pages in Testing mode point to Go-live readiness."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    browser, _ = signed_in()
    _opened(browser, "delivery_control", navigation.PRODUCTION_ONLY, "go_live")
    _opened(browser, "production_progress", navigation.NOT_CONFIRMED, "go_live")
    # HEAD is answered the same way, without the page's body.
    assert browser.head(f"/admin/{_path('delivery_control')}", **PAGE).status_code == (
        409
    )


def test_structure_fixed_once_live(auth_service, google):
    """Share options after go-live: the page and an old form's Review say why."""
    store = auth_service.store
    row, previous, path = financial_draft(store)
    browser, _ = signed_in()
    data = data_for(previous) | {
        "base_digest": store.active().digest,
        "options-0-label": "Changed",
    }
    page = browser.get(path, **PAGE)
    assert page.status_code == 200
    command(row, uuid4(), Action.ACTIVATE)
    _opened(browser, "share_settings", navigation.STRUCTURE_FIXED)
    # A Review sent from the page opened before go-live says the same.
    csrf = browser.cookies["pk_admin_csrf"].value
    refused = browser.post(path, {**data, "csrfmiddlewaretoken": csrf}, **PAGE)
    assert refused.status_code == 409
    refused = refused.content.decode()
    assert f"{navigation.STRUCTURE_FIXED}." in refused
    assert STALE.decode() not in refused
    # The campaign went live, so Go-live readiness leads to its activation.
    _opened(browser, "go_live", navigation.ALREADY_LIVE, "production_progress")


def test_archived_campaign(auth_service, google):
    """An archived current campaign's editors name it."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    with unguarded(), connection.cursor() as cursor:
        cursor.execute("UPDATE public.stewardship_campaign SET state='archived'")
    assert Campaign.objects.get().state == "archived"
    browser, _ = signed_in()
    _opened(browser, "content_catalog", navigation.ARCHIVED)
    _opened(browser, "artwork_settings", navigation.ARCHIVED)


def test_a_role_without_the_page_is_refused_not_told_why(auth_service, google):
    """Staff never see Share options in the menu, so its page refuses them
    (403) as before; a report they may open greyed out still says why."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    _staff(store, google)
    browser, _ = signed_in()
    hidden = browser.get(f"/admin/{_path('share_settings')}", **PAGE)
    assert hidden.status_code == 403
    assert b"isn&#x27;t available right now" not in hidden.content
    # Staff are not offered Campaign settings, so there is no link onward.
    _opened(browser, "financial_report", navigation.FINANCIAL_MISSING)


def test_a_signed_out_visitor_is_not_told_why(auth_service, google):
    """No session: the page's own refusal (sign in again), never the reason."""
    from django.test import Client

    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    response = Client().get(f"/admin/{_path('delivery_control')}", **PAGE)
    assert response.status_code == 403
    assert str(navigation.PRODUCTION_ONLY).encode() not in response.content


def test_a_command_sessions_key_is_refused_once(auth_service, google):
    """A command session's key in a browser is refused by the page's own
    check, once: the reason check never admits it, so the misuse is audited,
    noticed and alerted a single time."""
    from django.test import Client

    from parishkit.stewardship.accounts.automation_models import AutomationNotice
    from parishkit.stewardship.audit.models import AuditEvent

    from .automation_builders import command, paired

    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    intruder = Client(enforce_csrf_checks=True)
    intruder.cookies["pk_admin"] = caller.session.session_key
    # No current campaign, so Campaign settings is greyed out.
    response = intruder.get(f"/admin/{_path('campaign_settings')}", **PAGE)
    assert response.status_code != 409
    assert b"isn&#x27;t available right now" not in response.content
    row.refresh_from_db()
    assert row.end_reason == "misused"
    refused = AuditEvent.objects.filter(
        event_type="automation_session_refused", subject_id=row.pk
    )
    assert refused.count() == 1
    assert (
        AutomationNotice.objects.filter(kind="refused", automation_session=row).count()
        == 1
    )
