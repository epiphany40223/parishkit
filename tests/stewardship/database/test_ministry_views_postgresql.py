"""Real signed Google sessions exercise the Ministry activity web workflow."""

import re
from html import unescape
from uuid import uuid4

import pytest
from django.core import signing
from django.test import Client

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_models import MinistryActivity
from parishkit.stewardship.accounts.ministry_views import SALT
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address
from ..test_source_corpus import source
from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_current_chair_postgresql import publish
from .test_source_families_postgresql import source_singletons  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/configuration/ministries"


def post(browser, values):
    """Use the real CSRF cookie for each mutation, including signed confirmation."""
    return browser.post(
        URL, values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


def preview(browser, *, active="no"):
    """Extract the opaque signed confirmation from the rendered HTML form."""
    response = post(
        browser, {"action": "preview", "ministry_duid": "4", "active": active}
    )
    assert response.status_code == 200, response.content
    return unescape(
        re.search(r'name="preview" value="([^"]+)"', response.content.decode()).group(1)
    )


def test_admin_can_preview_apply_and_reactivate_without_optimistic_saved_claim(
    auth_service, google
):
    """Only a real installer activation changes applied Ministry policy."""
    publish(source())
    browser, _ = signed_in()
    assert browser.get(URL).status_code == 200
    token = preview(browser)
    assert not ConfigurationChangeRequest.objects.exists()
    response = post(browser, {"action": "confirm", "preview": token})
    assert response.status_code == 302
    request = ConfigurationChangeRequest.objects.get()
    pending = browser.get(response["Location"]).content
    assert b"Applying" in pending
    # A pending status page reloads itself until the installer finishes.
    assert b"data-live-pending" in pending
    assert not MinistryActivity.objects.exists()
    receipt = install_request(
        auth_service.store, request_id=request.pk, correlation_id=uuid4()
    )
    assert receipt.state == "applied"
    applied = browser.get(response["Location"]).content
    assert b"Applied" in applied
    assert b"data-live-pending" not in applied
    runtime = SystemConfiguration.objects.get()
    policy = MinistryActivity.objects.get(
        configuration_id=runtime.active_configuration_id
    )
    assert not policy.active and policy.ministry_duid == 4
    assert (
        post(browser, {"action": "confirm", "preview": token})["Location"]
        == response["Location"]
    )
    assert ConfigurationChangeRequest.objects.count() == 1
    second = post(
        browser, {"action": "confirm", "preview": preview(browser, active="yes")}
    )
    assert second.status_code == 302
    new = ConfigurationChangeRequest.objects.exclude(pk=request.pk).get()
    assert (
        install_request(
            auth_service.store, request_id=new.pk, correlation_id=uuid4()
        ).state
        == "applied"
    )
    runtime.refresh_from_db()
    reactivated = MinistryActivity.objects.get(
        configuration_id=runtime.active_configuration_id
    )
    assert reactivated.active and reactivated.record_id == policy.record_id


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_non_admin_cannot_read_preview_or_apply_ministry_policy(
    auth_service, google, role
):
    """Knowing catalog IDs or belonging to the Ministry never grants configuration."""
    publish(source())
    row = address("reader@example.org", roles=(role,))
    store = auth_service.store
    change(
        store,
        store.active(),
        store.active().version_id,
        [{"operation": "add", "section": "login_rules", **row}],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    assert browser.get(URL).status_code == 403
    assert (
        post(
            browser, {"action": "preview", "ministry_duid": 4, "active": "no"}
        ).status_code
        == 403
    )


def test_changed_source_or_configuration_invalidates_unapplied_preview(
    auth_service, google
):
    """A signed preview is an exact proposal, not a reusable authorization token."""
    publish(source())
    browser, _ = signed_in()
    token = preview(browser)
    publish(source())
    assert post(browser, {"action": "confirm", "preview": token}).status_code == 409
    assert not ConfigurationChangeRequest.objects.exists()


def test_csrf_and_signature_checks_cannot_be_skipped(auth_service, google):
    """Anonymous callers and forged confirmation tokens cannot create requests."""
    publish(source())
    browser, _ = signed_in()
    assert (
        browser.post(
            URL, {"action": "preview", "ministry_duid": 4, "active": "no"}
        ).status_code
        == 403
    )
    assert (
        post(
            browser, {"action": "confirm", "preview": "forged-private-input"}
        ).status_code
        == 400
    )
    assert Client().get(URL).status_code == 403
    assert not ConfigurationChangeRequest.objects.exists()


@pytest.mark.parametrize("query", ["?q=absent", "?state=inactive"])
def test_empty_search_is_clear_and_nonmutating(auth_service, google, query):
    """Search and activity filters do not change the source or local policy."""
    publish(source())
    browser, _ = signed_in()
    response = browser.get(URL + query)
    assert response.status_code == 200 and b"No Ministries match" in response.content
    assert not ConfigurationChangeRequest.objects.exists()


@pytest.mark.parametrize(
    "query",
    [
        "?page=word",
        "?page=-1",
        "?size=7",
        "?state=bad",
        "?state=all&state=active",
        "?extra=1",
        "?q=" + "x" * 201,
    ],
)
def test_invalid_filters_fail_closed(auth_service, google, query):
    """Malformed or out-of-range filters are client errors, not service outages."""
    publish(source())
    browser, _ = signed_in()
    assert browser.get(URL + query).status_code == 400
    assert not ConfigurationChangeRequest.objects.exists()


@pytest.mark.parametrize(
    "values",
    [
        {
            "action": "preview",
            "ministry_duid": "4",
            "active": "no",
            "actor": "override",
        },
        {"action": "preview", "ministry_duid": ["4", "999"], "active": "no"},
        {"action": "preview", "active": "no"},
        {"action": "preview", "ministry_duid": "4", "active": "maybe"},
        {"action": "preview", "ministry_duid": "0", "active": "no"},
        {"action": "unknown"},
        {"action": "confirm", "preview": "forged", "active": "no"},
    ],
)
def test_stray_repeated_and_invalid_form_values_are_rejected(
    auth_service, google, values
):
    """Hidden inputs never become additional accepted configuration controls."""
    publish(source())
    browser, _ = signed_in()
    assert post(browser, values).status_code == 400
    assert not ConfigurationChangeRequest.objects.exists()


def test_expired_or_wrong_actor_preview_cannot_create_request(
    auth_service, google, monkeypatch
):
    """A genuine signature is insufficient after expiry or for a different Admin."""
    publish(source())
    browser, _ = signed_in()
    token = preview(browser)
    intent = signing.loads(token, salt=SALT)
    foreign = signing.dumps(intent | {"actor": str(uuid4())}, salt=SALT)
    assert post(browser, {"action": "confirm", "preview": foreign}).status_code == 403
    signed_at = signing.time.time()
    monkeypatch.setattr(signing.time, "time", lambda: signed_at + 901)
    expired = post(browser, {"action": "confirm", "preview": token})
    assert expired.status_code == 409
    assert "out of date" in expired.json()["refusal"]["message"]
    assert not ConfigurationChangeRequest.objects.exists()


def test_configuration_change_requires_new_preview(auth_service, google):
    """A source-stable preview cannot overwrite another Admin's newer YAML version."""
    publish(source())
    browser, _ = signed_in()
    token = preview(browser)
    store = auth_service.store
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
                "values": {"name": "Changed parish"},
            }
        ],
    )
    assert post(browser, {"action": "confirm", "preview": token}).status_code == 409
    assert ConfigurationChangeRequest.objects.count() == 1


def test_status_reads_do_not_renew_idle_or_expose_other_actors(auth_service, google):
    """An open Applying page cannot keep an otherwise idle login alive."""
    publish(source())
    browser, _ = signed_in()
    response = post(browser, {"action": "confirm", "preview": preview(browser)})
    row = PortalSession.objects.get(revoked_at__isnull=True)
    activity = row.last_activity_at
    for _ in range(2):
        assert browser.get(response["Location"]).status_code == 200
    row.refresh_from_db()
    assert row.last_activity_at == activity
    assert browser.get(f"/admin/configuration/requests/{uuid4()}").status_code == 404


def test_real_web_role_can_use_catalog_and_intake_without_source_write_grants(
    auth_service, google
):
    """Exercise the actual web SQL inventory, not the test database owner's rights."""
    publish(source())
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        assert browser.get(URL).status_code == 200
        token = preview(browser)
        response = post(browser, {"action": "confirm", "preview": token})
        assert response.status_code == 302
        assert browser.get(response["Location"]).status_code == 200


def two_ministries():
    """The standard source plus a second Ministry without members."""
    data = source()
    data.ministry_types[5] = {"id": 5, "name": "Lectors"}
    data.ministry_type_memberships[5] = {"membership": []}
    return data


def test_ministry_table_pages_and_keeps_filters(auth_service, google):
    """The shared navigator pages the table and a page past the end shows the last."""
    publish(two_ministries())
    browser, _ = signed_in()
    body = browser.get(URL + "?size=25&page=9").content.decode()
    assert "Showing 1–2 of 2" in body and 'name="page" value="1"' in body
    assert "data-select-all" in body and "data-bulk-action" in body
    body = browser.get(URL + "?q=5&state=active&size=all").content.decode()
    assert "Lectors" in body and "Choir" not in body
    assert 'name="q" value="5"' in body


def test_bulk_change_is_one_request_and_skips_unchanged_rows(auth_service, google):
    """Selected Ministries change together through one audited request."""
    publish(two_ministries())
    browser, _ = signed_in()
    response = post(
        browser, {"action": "preview", "ministry_duid": ["4", "5"], "active": "no"}
    )
    assert response.status_code == 200
    assert b"Inactivate 2 Ministries" in response.content
    token = unescape(
        re.search(r'name="preview" value="([^"]+)"', response.content.decode()).group(1)
    )
    assert len(signing.loads(token, salt=SALT)["patch"]) == 2
    confirmed = post(browser, {"action": "confirm", "preview": token})
    assert confirmed.status_code == 302
    request = ConfigurationChangeRequest.objects.get()
    install_request(auth_service.store, request_id=request.pk, correlation_id=uuid4())
    runtime = SystemConfiguration.objects.get()
    assert set(
        MinistryActivity.objects.filter(
            configuration_id=runtime.active_configuration_id
        ).values_list("ministry_duid", "active")
    ) == {(4, False), (5, False)}
    again = post(
        browser, {"action": "preview", "ministry_duid": ["4", "5"], "active": "no"}
    )
    assert b"Nothing to change" in again.content
    assert b'name="preview"' not in again.content


def test_empty_selection_explains_itself(auth_service, google):
    """A bulk action without a selection re-shows the table with a plain message."""
    publish(source())
    browser, _ = signed_in()
    response = post(browser, {"action": "preview", "active": "no"})
    assert response.status_code == 400
    assert b"Select at least one Ministry" in response.content
