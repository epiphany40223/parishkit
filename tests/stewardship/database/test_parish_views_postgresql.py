"""Profile edits exercise real YAML installation and campaign isolation."""

import re
from html import unescape
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.models import PortalSession
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
# The header ui-v1.js sends with every in-place request.
IN_PLACE = {"HTTP_X_REQUESTED_WITH": "fetch"}


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
    """The change a confirmation's in-place answer names (#532).

    Confirmation answers with Parish settings again, naming the request and
    the review region, not the separate Change status page.
    """
    assert response.status_code == 302
    location = urlsplit(response["Location"])
    assert (location.path, location.fragment) == (URL, "settings-review")
    return UUID(parse_qs(location.query)["request"][0])


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
    assert (
        response.status_code == 400 and b"No settings have changed" in response.content
    )
    assert post(browser, {"action": "confirm", "preview": "forged"}).status_code == 400
    assert not ConfigurationChangeRequest.objects.exists()
    assert SystemConfiguration.objects.get().mode == "testing"


def digest(response):
    """The version a rendered settings page's form would send."""
    return re.search(
        r'name="base_digest" value="([0-9a-f]*)"', response.content.decode()
    ).group(1)


def region(body):
    """The in-place review region of a rendered settings page."""
    body = body.decode()
    start = body.index('<div id="settings-review" data-in-place-region>')
    return body[start : body.index("</div>", start)]


def test_review_apply_and_status_stay_on_the_settings_page(auth_service, google):
    """Review, apply and the change's status all render on Parish settings (#532).

    The review keeps its exact content (each change, before and after, and
    the timezone note) beside the form; the confirmed change's status polls
    Change status's passive read, whose settled status carries the in-place
    follow-up back to this page only.
    """
    store = auth_service.store
    browser, _ = signed_in()
    values = fields(store, name="Renamed Parish", timezone="America/Los_Angeles")
    review = post(browser, values)
    shown = region(review.content)
    assert b'id="settings-form"' in review.content
    assert "Review your changes" in shown and "Apply changes" in shown
    assert "Renamed Parish" in shown and "America/Los_Angeles" in shown
    assert "This timezone change is prospective." in shown
    response = post(browser, {"action": "confirm", "preview": token(review)})
    request_id = requested(response)
    # Reading the change's status (the page's own quiet refresh once it is
    # applied) is passive: it never renews idle time, as Change status.
    activity = PortalSession.objects.get().last_activity_at
    page = browser.get(response["Location"])
    assert page.status_code == 200 and page["Cache-Control"] == "no-store"
    assert PortalSession.objects.get().last_activity_at == activity
    shown = region(page.content)
    assert "Applying your change" in shown and "data-live-pending" in shown
    status = reverse("admin:configuration_request", args=[request_id])
    assert f'data-live-url="{status}?in_place=parish_settings"' in shown
    install_request(store, request_id=request_id, correlation_id=uuid4())
    assert "Applied: your change is saved" in region(
        browser.get(response["Location"]).content
    )
    follow = (
        f"<a hidden data-live-follow data-in-place data-in-place-quiet "
        f'href="{URL}?request={request_id}#settings-review">'
    )
    assert follow.encode() in browser.get(f"{status}?in_place=parish_settings").content
    # Only the in-place settings pages are followed; Change status itself
    # still never leaves on its own.
    for query in ("", "?in_place=configuration_request", "?in_place=//example.org"):
        assert b"data-live-follow" not in browser.get(status + query).content
    # A request that is not this Administrator's, or not a request id at all.
    assert browser.get(f"{URL}?request={uuid4()}").status_code == 404
    assert browser.get(f"{URL}?request=nonsense").status_code == 400
    assert browser.get(f"{URL}?other=1").status_code == 400


def test_refusals_render_in_the_review_region(auth_service, google):
    """A refused review or confirmation is the page again, explained in place.

    Field errors are summarized with links to their fields, since the form
    itself is not replaced in place; a stale page and an out-of-date preview
    say how to recover.
    """
    store = auth_service.store
    browser, _ = signed_in()
    refused = post(browser, fields(store, phone="123"))
    assert refused.status_code == 400
    shown = region(refused.content)
    assert "data-error-summary" in shown
    assert '<a href="#id_phone">Parish telephone: Enter a ten-digit US' in shown
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
    current = store.active().digest
    stale = post(browser, values)
    assert stale.status_code == 409
    assert "This page changed in another tab or session." in region(stale.content)
    expired = post(browser, {"action": "confirm", "preview": proposal})
    assert expired.status_code == 409
    assert "This preview is out of date." in region(expired.content)
    # Both refusals keep the form at the version the reader's values came
    # from, never the current one: only the form's hidden fields are taken
    # from an in-place answer, so the current version would let the next
    # Review quietly propose undoing the other tab's change.
    for refused in (stale, expired):
        assert digest(refused) == values["base_digest"] != current
    # So a second Review, as the refused page's form would send it, is still
    # refused until the page is reloaded, which brings the current version.
    again = post(browser, values | {"base_digest": digest(expired)})
    assert again.status_code == 409
    assert "This page changed in another tab or session." in region(again.content)
    assert digest(browser.get(URL)) == current
    assert ConfigurationChangeRequest.objects.count() == requests


def test_a_pending_change_keeps_the_form_at_its_reviewed_version(auth_service, google):
    """Apply's answer and its status refreshes draw the form at the version
    the change was reviewed at until it is applied (#751 review), never a
    version committed elsewhere meanwhile, which would pair the reader's
    values with settings they never saw; once applied, at its own version."""
    store = auth_service.store
    browser, _ = signed_in()
    values = fields(store, name="Renamed Parish")
    response = post(
        browser, {"action": "confirm", "preview": token(post(browser, values))}
    )
    request_id = requested(response)
    assert (
        digest(browser.get(response["Location"], **IN_PLACE)) == values["base_digest"]
    )
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
                "values": {"website": "https://elsewhere.example.org"},
            }
        ],
    )
    current = store.active().digest
    assert (
        digest(browser.get(response["Location"], **IN_PLACE))
        == values["base_digest"]
        != current
    )
    # The change, reviewed against the older settings, is then not applied,
    # and the form stays at the version its values came from.
    install_request(store, request_id=request_id, correlation_id=uuid4())
    settled = browser.get(response["Location"], **IN_PLACE)
    assert "Not applied" in region(settled.content)
    assert digest(settled) == values["base_digest"]
    # A full load of the same address (a reload) draws the current settings
    # at the current version, so the next Review is accepted (#751 review).
    reloaded = browser.get(response["Location"])
    assert digest(reloaded) == current
    again = post(
        browser, fields(store, name="After reload") | {"base_digest": digest(reloaded)}
    )
    assert again.status_code == 200 and "Review your changes" in region(again.content)


def test_an_applied_change_draws_the_form_at_its_applied_version(auth_service, google):
    """Once applied, the status page's form is at the version the change made."""
    store = auth_service.store
    browser, _ = signed_in()
    values = fields(store, name="Renamed Parish")
    response = post(
        browser, {"action": "confirm", "preview": token(post(browser, values))}
    )
    install_request(store, request_id=requested(response), correlation_id=uuid4())
    applied = store.active().digest
    assert applied != values["base_digest"]
    assert digest(browser.get(response["Location"], **IN_PLACE)) == applied
