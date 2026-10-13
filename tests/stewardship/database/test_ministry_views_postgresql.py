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
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_background_grants_postgresql import task_login
from .test_campaign_views_postgresql import requested
from .test_parish_views_postgresql import region
from .test_source_families_postgresql import (
    publish,
    source_singletons,  # noqa: F401
)

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/parish/ministries/"


def post(browser, values, query=""):
    """Use the real CSRF cookie for each mutation, including signed confirmation.

    ``query`` is the page's view (filters, sort, page) the form posts from.
    """
    return browser.post(
        URL + query,
        values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value},
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
    listing = browser.get(URL)
    assert listing.status_code == 200
    assert flow_steps(listing.content) == (STEPS, "Make changes")
    review = post(browser, {"action": "preview", "ministry_duid": "4", "active": "no"})
    assert flow_steps(review.content) == (STEPS, "Review")
    # Reviewed in place (#750): the change and Apply fill the review region
    # under the table.
    shown = unescape(region(review.content))
    assert "Inactivate 1 Ministry" in shown and "Apply changes" in shown
    token = preview(browser)
    assert not ConfigurationChangeRequest.objects.exists()
    response = post(browser, {"action": "confirm", "preview": token})
    assert response.status_code == 302
    request = ConfigurationChangeRequest.objects.get()
    # Apply answers with this page, naming the change, not Change status.
    assert response["Location"] == f"{URL}?request={request.pk}#settings-review"
    pending = browser.get(response["Location"])
    assert flow_steps(pending.content) == (STEPS, "Apply")
    assert "Applying" in region(pending.content)
    # The pending status polls Change status until the installer finishes.
    assert "data-live-pending" in region(pending.content)
    assert not MinistryActivity.objects.exists()
    receipt = install_request(
        auth_service.store, request_id=request.pk, correlation_id=uuid4()
    )
    assert receipt.state == "applied"
    applied = region(browser.get(response["Location"]).content)
    assert "Applied: your change is saved" in applied
    assert "data-live-pending" not in applied
    # Change status itself still leads back to Ministries.
    status = browser.get(f"/admin/changes/{request.pk}/").content
    assert f'<a href="{URL}">Return to Ministries</a>'.encode() in status
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
    # Refused in place (#750): the page again, explained in its review region.
    assert "This preview is out of date." in region(expired.content)
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
    assert browser.get(f"/admin/changes/{uuid4()}/").status_code == 404


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
    body = browser.get(URL + "?q=Lect&state=active&size=all").content.decode()
    assert "Lectors" in body and "Choir" not in body
    assert 'name="q" value="Lect"' in body
    # Search is by name only: a Ministry DUID finds nothing (#1123).
    body = browser.get(URL + "?q=5&state=all&size=all").content.decode()
    assert "No Ministries match these filters." in body and "Lectors" not in body


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
    # Review only a change: nothing selected would change, said in place.
    assert again.status_code == 400
    assert "already inactive, so there is nothing to change" in region(again.content)
    assert b'name="preview"' not in again.content


def test_empty_selection_explains_itself(auth_service, google):
    """A bulk action without a selection re-shows the table with a plain message."""
    publish(source())
    browser, _ = signed_in()
    response = post(browser, {"action": "preview", "active": "no"})
    assert response.status_code == 400
    assert "Select at least one Ministry" in region(response.content)


def test_the_table_view_is_kept_through_review_apply_and_follow_up(
    auth_service, google
):
    """The filters, sort and page the Administrator was looking at (#750)
    stay in Apply's answer, in Change status's read and in the quiet
    follow-up once the change is applied, which draws that same view."""
    publish(two_ministries())
    browser, _ = signed_in()
    query = "?q=Lect&state=all&size=25&sort=-name"
    review = post(
        browser, {"action": "preview", "ministry_duid": "5", "active": "no"}, query
    )
    assert review.status_code == 200
    assert 'name="q" value="Lect"' in review.content.decode()
    token = unescape(
        re.search(r'name="preview" value="([^"]+)"', review.content.decode()).group(1)
    )
    confirmed = post(browser, {"action": "confirm", "preview": token}, query)
    request = requested(confirmed)
    location = confirmed["Location"]
    assert location.startswith(URL + "?q=Lect&state=all&size=25&sort=-name&request=")
    pending = browser.get(location)
    assert pending.status_code == 200
    status_url = unescape(
        re.search(r'data-live-url="([^"]+)"', pending.content.decode()).group(1)
    )
    install_request(auth_service.store, request_id=request, correlation_id=uuid4())
    read = browser.get(status_url).content.decode()
    follow = unescape(re.search(r'data-live-follow[^>]*href="([^"]+)"', read).group(1))
    assert follow.startswith(URL + "?q=Lect&state=all&size=25&sort=-name&request=")
    settled = browser.get(follow.split("#")[0]).content.decode()
    assert "Lectors" in settled and "Choir" not in settled
    assert "Applied: your change is saved" in settled
