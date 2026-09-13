"""Profile edits exercise real YAML installation and campaign isolation."""

import re
from html import unescape
from uuid import uuid4

import pytest

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
URL = "/admin/configuration/parish"


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
        URL, values | {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value}
    )


def token(response):
    """Confirm only server-rendered signed intent rather than fabricating a patch."""
    assert response.status_code == 200, response.content
    return unescape(
        re.search(r'name="preview" value="([^"]+)"', response.content.decode()).group(1)
    )


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
    assert response.status_code == 302
    assert store.active() == before
    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
    )
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
        assert response.status_code == 302
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
