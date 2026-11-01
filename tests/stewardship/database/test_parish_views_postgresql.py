"""Profile edits exercise real YAML installation and campaign isolation."""

import re
from html import unescape
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.models import Campaign, ScheduleRevision
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import add_draft, change
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/parish/settings/"


def fields(store, **changes):
    """Post only the explicitly rendered scalar profile fields and its applied base."""
    version = store.active()
    profile = version.document()["sections"]["parish"][0]["values"]
    return (
        {name: profile[name] for name in ("name", "website", "timezone", "phone")}
        | {
            "base_digest": version.digest,
            "action": "preview",
        }
        | changes
    )


def post(browser, values):
    """Use a genuine authenticated CSRF cookie for every proposed mutation."""
    return browser.post(
        URL, values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


def token(response):
    """Confirm only server-rendered signed intent rather than fabricating a patch."""
    assert response.status_code == 200, response.content
    return unescape(
        re.search(r'name="preview" value="([^"]+)"', response.content.decode()).group(1)
    )


def requested(response):
    """The change a confirmation names: it answers with Change status (#750).

    The review dialog's Apply follows that page itself, so Parish settings
    no longer answers its own confirmation.
    """
    assert response.status_code == 302, response.content
    location = urlsplit(response["Location"])
    request_id = UUID(location.path.rstrip("/").rsplit("/", 1)[-1])
    assert location.path == reverse("admin:configuration_request", args=[request_id])
    return request_id


def test_profile_apply_is_durable_idempotent_and_does_not_change_campaign_timezone(
    auth_service, google
):
    """The Parish default changes, while campaign boundaries/schedules remain pinned."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    before = store.active()
    campaign = Campaign.objects.get()
    projection = campaign.active_configuration
    old_schedules = list(
        ScheduleRevision.objects.filter(configuration_id=before.version_id).values(
            "record_id", "due_at", "values"
        )
    )
    browser, _ = signed_in()
    assert browser.get(URL).status_code == 200
    values = fields(store, name="New Parish", timezone="America/Los_Angeles")
    proposal = token(post(browser, values))
    response = post(browser, {"action": "confirm", "preview": proposal})
    assert store.active() == before
    request = ConfigurationChangeRequest.objects.get(pk=requested(response))
    receipt = install_request(store, request_id=request.pk, correlation_id=uuid4())
    assert receipt.state == "applied"
    assert (
        store.active().document()["sections"]["parish"][0]["values"]["timezone"]
        == "America/Los_Angeles"
    )
    campaign.refresh_from_db()
    current = campaign.active_configuration
    assert (current.timezone, current.starts_at, current.ends_at, current.values) == (
        projection.timezone,
        projection.starts_at,
        projection.ends_at,
        projection.values,
    )
    schedules = list(
        ScheduleRevision.objects.filter(
            configuration_id=store.active().version_id
        ).values("record_id", "due_at", "values")
    )
    assert schedules == old_schedules
    assert (
        store.active().document()["sections"]["parish"][0]["values"]["branding"]
        == before.document()["sections"]["parish"][0]["values"]["branding"]
    )
    assert (
        post(browser, {"action": "confirm", "preview": proposal})["Location"]
        == response["Location"]
    )
    assert b"Applied" in browser.get(response["Location"]).content


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"name": "x" * 255},
        {"name": "bad\x00name"},
        {"website": "https://example.org/?credential=value"},
        {"website": "https://user:password@example.org/"},
        {"website": "javascript:alert(1)"},
        {"timezone": "Mars/Parish"},
        {"phone": "123"},
        {"phone": ""},
        {"base_digest": "bad"},
        {"branding": "unexpected"},
        {"mode": "production"},
    ],
)
def test_invalid_and_stray_profile_fields_do_not_create_requests(
    auth_service, google, changes
):
    """HTML and complete YAML validation agree before any durable mutation."""
    browser, _ = signed_in()
    response = post(browser, fields(auth_service.store, **changes))
    assert response.status_code == 400
    assert not ConfigurationChangeRequest.objects.exists()


def test_old_editor_base_and_old_confirmation_are_rejected(auth_service, google):
    """Neither the edit form nor its confirmation can silently rebase another save."""
    browser, _ = signed_in()
    store = auth_service.store
    values = fields(store, name="First edit")
    proposal = token(post(browser, values))
    version = store.active()
    parish = version.document()["sections"]["parish"][0]
    change(
        store,
        version,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"name": "Concurrent edit"},
            }
        ],
    )
    assert post(browser, values).status_code == 409
    assert post(browser, {"action": "confirm", "preview": proposal}).status_code == 409
    assert ConfigurationChangeRequest.objects.count() == 1


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_configuration_is_not_exposed_to_non_admin_roles(auth_service, google, role):
    """A direct URL or forged form never confers configuration authority."""
    store = auth_service.store
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
    assert browser.get(URL).status_code == 403
    assert post(browser, fields(store, name="Forbidden edit")).status_code == 403


def test_real_web_sql_grants_support_profile_preview_and_intake(auth_service, google):
    """The web role reads projections and submits intent, never installs settings."""
    browser, _ = signed_in()
    values = fields(auth_service.store, name="Profile edit")
    with task_login(ServiceRole.WEB):
        assert browser.get(URL).status_code == 200
        proposal = token(post(browser, values))
        response = post(browser, {"action": "confirm", "preview": proposal})
        requested(response)
        assert b"Applying" in browser.get(response["Location"]).content


def test_unchanged_form_and_forged_confirmation_are_not_saved(auth_service, google):
    """No-op input is clear to the user and signatures cannot be bypassed."""
    browser, _ = signed_in()
    response = post(browser, fields(auth_service.store))
    # A plain refusal for the review dialog (#750); the page's change gate
    # keeps it out of reach, so only a request bypassing the script meets it.
    assert response.status_code == 400
    assert response.json()["refusal"]["message"] == "No settings have changed."
    assert post(browser, {"action": "confirm", "preview": "forged"}).status_code == 400
    assert not ConfigurationChangeRequest.objects.exists()
    assert SystemConfiguration.objects.get().mode == "testing"


def digest(response):
    """The version a rendered settings page's form would send."""
    return re.search(
        r'name="base_digest" value="([0-9a-f]*)"', response.content.decode()
    ).group(1)


def test_review_opens_the_dialog_and_apply_follows_change_status(auth_service, google):
    """Review answers the review dialog's fragment, never a page (#750).

    It keeps the review's exact content (a compact review, #1356: one
    "Setting: old → new" line per change, and the timezone note) and the
    signed preview; Apply answers with Change
    status, which the dialog follows. The page draws the dialog, its status
    line and the form's region, redrawn from the page's own address, and
    takes no query: there are no legacy in-place addresses.
    """
    store = auth_service.store
    browser, _ = signed_in()
    page = browser.get(URL)
    html = page.content.decode()
    assert page.status_code == 200 and page["Cache-Control"] == "no-store"
    assert f'data-refresh-url="{URL}"' in html
    assert 'data-review-open="parish-review"' in html
    assert 'id="parish-outcome"' in html and 'id="settings-review"' not in html
    values = fields(store, name="Renamed Parish", timezone="America/Los_Angeles")
    review = post(browser, values)
    shown = review.content.decode()
    assert "data-review-answer" in shown and "data-review-preview" in shown
    assert 'id="settings-form"' not in shown and "settings-review" not in shown
    assert "Renamed Parish" in shown and "America/Los_Angeles" in shown
    assert '<ul class="compact-review" id="parish-review-summary">' in shown
    assert shown.count("<li>") == 2 and "Proposed:" not in shown
    assert "This time zone change is prospective." in shown
    response = post(browser, {"action": "confirm", "preview": token(review)})
    request_id = requested(response)
    status = browser.get(response["Location"]).content
    assert b"Applying your change" in status
    install_request(store, request_id=request_id, correlation_id=uuid4())
    assert b"Applied: your change is saved" in browser.get(response["Location"]).content
    # The page's own address, redrawn after Apply, is at the applied version.
    assert digest(browser.get(URL)) == store.active().digest
    for query in (f"?request={request_id}", f"?request={uuid4()}", "?other=1"):
        assert browser.get(URL + query).status_code == 400


def test_refusals_are_for_the_dialog_or_the_field(auth_service, google):
    """A field's refusal is the page again (400), its summary linked to the
    field, so the dialog stays shut; a stale page and an out-of-date preview
    are plain refusals shown in the dialog, which never redraw the form.
    """
    store = auth_service.store
    browser, _ = signed_in()
    refused = post(browser, fields(store, phone="123"))
    assert refused.status_code == 400
    html = refused.content.decode()
    assert 'id="settings-form"' in html and "data-error-summary" in html
    assert '<a href="#id_phone">Parish telephone: Enter a ten-digit US' in html
    values = fields(store, name="First edit")
    proposal = token(post(browser, values))
    parish = store.active().document()["sections"]["parish"][0]
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"name": "Concurrent edit"},
            }
        ],
    )
    requests = ConfigurationChangeRequest.objects.count()
    stale = post(browser, values)
    assert stale.status_code == 409
    refusal = stale.json()["refusal"]
    assert refusal["message"] == "This page changed in another tab or session."
    assert "Reload the page" in refusal["fix"]
    expired = post(browser, {"action": "confirm", "preview": proposal})
    assert expired.status_code == 409
    assert expired.json()["refusal"]["message"] == "This preview is out of date."
    assert digest(browser.get(URL)) == store.active().digest
    assert ConfigurationChangeRequest.objects.count() == requests


def test_a_refused_website_is_shown_at_its_field(auth_service, google):
    """A website with a query or a user name is refused at the field, with
    the summary linking to it, not as a notice above the form (#1330)."""
    store = auth_service.store
    browser, _ = signed_in()
    for website in ("https://example.org/?a=1", "https://user@example.org/"):
        refused = post(browser, fields(store, website=website))
        assert refused.status_code == 400
        html = refused.content.decode()
        (field,) = re.findall(r'<input[^>]*name="website"[^>]*>', html)
        assert 'aria-invalid="true"' in field and "id_website_error" in field
        assert '<a href="#id_website">Parish website: Use a website URL' in html
        assert "Website URLs cannot include" not in html
    assert not ConfigurationChangeRequest.objects.exists()


@pytest.mark.parametrize(
    "stored",
    [
        "https://192.168.1.10/",
        "https://[2001:db8::1]:8443/",
        "https://localhost:3000/",
        "https://example.org./",
    ],
)
def test_a_stored_host_never_blocks_an_unrelated_save(auth_service, google, stored):
    """A website and giving link already saved with an IP address, localhost
    or a trailing dot (the server's URL validator admits them) do not stop
    the Administrator changing another setting (#1577)."""
    store = auth_service.store
    browser, _ = signed_in()
    version = store.active()
    record = version.document()["sections"]["parish"][0]
    change(
        store,
        version,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": record["id"],
                "values": {"website": stored, "online_giving_url": stored},
            }
        ],
    )
    response = post(
        browser,
        fields(store, online_giving_url=stored, date_format="eu_long"),
    )
    assert response.status_code == 200, response.content
    assert b"aria-invalid" not in response.content
    assert token(response)


def test_a_stored_alias_is_kept_and_a_new_zone_is_stored_canonically(
    monkeypatch, request
):
    """A Parish set up with US/Eastern keeps it unless the zone is changed.

    Saving another setting leaves the alias (no timezone change, so no
    prospective-zone note); choosing a zone by an alias stores the zone it
    links to (#1286).
    """
    from .. import configuration_factory

    monkeypatch.setattr(configuration_factory, "PARISH_TIMEZONE", "US/Eastern")
    store = request.getfixturevalue("auth_service").store
    request.getfixturevalue("google")
    browser, _ = signed_in()
    page = browser.get(URL).content.decode()
    assert "US/Eastern (older name for America/New_York)" in page
    renamed = post(browser, fields(store, name="Renamed Parish"))
    assert renamed.status_code == 200, renamed.content
    assert b"This time zone change is prospective" not in renamed.content
    moved = post(browser, fields(store, timezone="Canada/Pacific"))
    proposal = token(moved)
    assert b"America/Vancouver" in moved.content
    response = post(browser, {"action": "confirm", "preview": proposal})
    install_request(store, request_id=requested(response), correlation_id=uuid4())
    assert (
        store.active().document()["sections"]["parish"][0]["values"]["timezone"]
        == "America/Vancouver"
    )
