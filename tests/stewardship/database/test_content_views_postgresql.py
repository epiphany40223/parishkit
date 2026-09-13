"""Real-session content editing, sanitization, immutable revisions and mail impacts."""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole

from ..content_factory import content
from ..policy_factory import address
from ..test_content_forms import fields
from .auth_builders import signed_in
from .campaign_builders import add_draft, change, command
from .test_background_grants_postgresql import task_login
from .test_campaign_views_postgresql import apply, post
from .test_parish_views_postgresql import token

pytestmark = pytest.mark.django_db(transaction=True)


def setup(store):
    """Begin with a real applied current draft and a legacy unresolved mail template."""
    result, _, schedule = add_draft(store, store.active(), uuid4())
    assert result.state == "applied"
    campaign = Campaign.objects.get()
    return campaign, f"/admin/campaign/{campaign.pk}/content", schedule


def values(store, **changes):
    """Submit HTML form values, not raw YAML patches or browser role declarations."""
    return fields(base_digest=store.active().digest, action="preview", **changes)


def test_page_content_apply_sanitizes_samples_and_replays(auth_service, google):
    """Preview emits sanitized fictional data; confirmation installs separately."""
    store = auth_service.store
    campaign, catalog, _ = setup(store)
    path = catalog + "/page/welcome"
    browser, _ = signed_in()
    assert browser.get(catalog).status_code == 200
    assert browser.get(path).status_code == 200
    response = post(
        browser,
        path,
        values(
            store,
            html='<p onclick="unsafe()">Hi {{ family_name }}</p>'
            "<script>steal()</script>",
        ),
    )
    assert b"Sample Family" in response.content and b"steal()" not in response.content
    proposal = token(response)
    accepted = post(browser, path, {"action": "confirm", "preview": proposal})
    apply(store, accepted)
    snapshot = SystemConfiguration.objects.get().active_configuration
    row = snapshot.content_versions.get()
    assert row.html == "<p>Hi {{ family_name }}</p>"
    assert row.text == "Hi {{ family_name }}"
    campaign.refresh_from_db()
    assert campaign.active_configuration.values["content_versions"]["welcome"] == str(
        row.record_id
    )
    assert (
        post(browser, path, {"action": "confirm", "preview": proposal})["Location"]
        == accepted["Location"]
    )
    assert b"Hi {{ family_name }}" in browser.get(path).content


@pytest.mark.parametrize(
    "changes",
    [
        {"html": "{{ unknown }}"},
        {"html": ["One", "Two"]},
        {"subject": "Unexpected page subject"},
        {"mode": "production"},
    ],
)
def test_content_invalid_fields_never_create_requests(auth_service, google, changes):
    """Unsupported placeholders and hidden/repeated fields fail before intake."""
    store = auth_service.store
    _, path, _ = setup(store)
    browser, _ = signed_in()
    count = ConfigurationChangeRequest.objects.count()
    assert (
        post(browser, path + "/page/welcome", values(store, **changes)).status_code
        == 400
    )
    assert ConfigurationChangeRequest.objects.count() == count


def test_email_edit_reconciles_subject_and_preserves_unrelated_templates(
    auth_service, google
):
    """One template update changes exactly its referenced schedules in one request."""
    store = auth_service.store
    campaign, catalog, schedule = setup(store)
    row = content(str(campaign.pk), kind="email", slot="initial")
    other = content(str(campaign.pk), kind="email", slot="initial", subject="Other")
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {"operation": "add", "section": "content", **row},
                {"operation": "add", "section": "content", **other},
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": schedule["id"],
                    "values": {
                        "template_version": row["id"],
                        "subject": row["values"]["subject"],
                    },
                },
            ],
        ).state
        == "applied"
    )
    browser, _ = signed_in()
    path = catalog + "/email/initial/" + row["id"]
    assert browser.get(path).status_code == 200
    preview = post(browser, path, values(store, subject="Revised invitation"))
    assert b"Schedules using this template" in preview.content
    apply(store, post(browser, path, {"action": "confirm", "preview": token(preview)}))
    snapshot = SystemConfiguration.objects.get().active_configuration
    assert snapshot.content_versions.count() == 2
    assert snapshot.schedule_revisions.get().values["subject"] == "Revised invitation"
    assert snapshot.content_versions.filter(record_id=other["id"]).exists()
    assert browser.get(path).status_code == 404


def test_email_create_and_explicit_page_remove_with_web_grants(auth_service, google):
    """Narrow web grants suffice for preview/intake, without installer authority."""
    store = auth_service.store
    campaign, catalog, _ = setup(store)
    browser, _ = signed_in()
    path = catalog + "/email/reminder"
    with task_login(ServiceRole.WEB):
        assert browser.get(catalog).status_code == 200
        assert browser.get(path).status_code == 200
        proposal = token(post(browser, path, values(store, subject="Reminder")))
        accepted = post(browser, path, {"action": "confirm", "preview": proposal})
    apply(store, accepted)
    page = content(str(campaign.pk), slot="login_help")
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "content", **page}],
        ).state
        == "applied"
    )
    path = catalog + "/page/login_help"
    proposal = token(post(browser, path, values(store, clear="on")))
    apply(store, post(browser, path, {"action": "confirm", "preview": proposal}))
    assert (
        not SystemConfiguration.objects.get()
        .active_configuration.content_versions.filter(kind="page")
        .exists()
    )


def test_nonstructural_content_edit_survives_lock_but_stale_preview_does_not(
    auth_service, google
):
    """Activation invalidates old previews without preventing fresh content edits."""
    store = auth_service.store
    campaign, catalog, _ = setup(store)
    browser, _ = signed_in()
    path = catalog + "/page/welcome"
    proposal = token(post(browser, path, values(store)))
    command(campaign, uuid4(), Action.ACTIVATE)
    assert (
        post(browser, path, {"action": "confirm", "preview": proposal}).status_code
        == 409
    )
    proposal = token(post(browser, path, values(store)))
    apply(store, post(browser, path, {"action": "confirm", "preview": proposal}))


def test_content_stale_base_noop_and_route_scope(auth_service, google):
    """A signed preview cannot be confirmed at a different slot or campaign URL."""
    store = auth_service.store
    _, catalog, _ = setup(store)
    browser, _ = signed_in()
    path = catalog + "/page/welcome"
    assert (
        post(browser, path, values(store) | {"base_digest": "f" * 64}).status_code
        == 409
    )
    assert post(browser, path, values(store, clear="on")).status_code == 400
    proposal = token(post(browser, path, values(store)))
    assert (
        post(
            browser,
            catalog + "/page/review",
            {"action": "confirm", "preview": proposal},
        ).status_code
        == 400
    )
    assert browser.get(catalog + "/page/financial").status_code == 404
    assert browser.get(catalog + "/unknown/welcome").status_code == 404
    assert browser.get(path + "?html=hidden").status_code == 400
    assert post(browser, catalog, values(store)).status_code == 400


def test_staff_cannot_read_or_edit_content(auth_service, google):
    """Staff's campaign reporting access does not imply configuration capability."""
    store = auth_service.store
    _, catalog, _ = setup(store)
    staff = address("staff@example.org", roles=("staff",))
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "login_rules", **staff}],
        ).state
        == "applied"
    )
    google[0]["email"] = "staff@example.org"
    browser, _ = signed_in()
    assert browser.get(catalog).status_code == 403
    assert post(browser, catalog + "/page/welcome", values(store)).status_code == 403
