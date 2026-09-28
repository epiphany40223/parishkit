"""Real-session content editing, sanitization, immutable revisions and mail impacts."""

from uuid import uuid4

import pytest
from django.test import Client

from parishkit.stewardship.accounts.models import PortalSession
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
    editor = browser.get(path)
    assert editor.status_code == 200
    # Web-only pages offer no plain-text controls (#259); typed text is ignored
    # and the stored plain text is always generated from the HTML.
    assert b"data-plain-text" not in editor.content
    response = post(
        browser,
        path,
        values(
            store,
            html='<p onclick="unsafe()">Hi {{ family_name }}</p>'
            "<script>steal()</script>",
            generate_text="",
            text="Typed text is ignored",
        ),
    )
    assert b"Hi Sample<" in response.content and b"steal()" not in response.content
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
    preview = post(
        browser,
        path,
        values(
            store,
            subject="Revised invitation",
            html="<p>{{ family_code }} {{ family_url }}</p>",
        ),
    )
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
        proposal = token(
            post(
                browser,
                path,
                values(
                    store,
                    subject="Reminder",
                    html="<p>{{ family_code }} {{ family_url }}</p>",
                ),
            )
        )
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


def test_receipt_template_edits_one_selection_and_retains_old_revision(
    auth_service, google
):
    """The direct-mail slot replaces its selection, not an arbitrary list member."""
    store = auth_service.store
    campaign, catalog, _ = setup(store)
    row = content(str(campaign.pk), kind="email", slot="confirmation", subject="Before")
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "content", **row}],
        ).state
        == "applied"
    )
    before = SystemConfiguration.objects.get().active_configuration
    browser, _ = signed_in()
    path = catalog + "/email/confirmation"
    with task_login(ServiceRole.WEB):
        assert b"Edit receipt template" in browser.get(catalog).content
        assert b"Before" in browser.get(path).content
        preview = post(browser, path, values(store, subject="After"))
        assert b"Submitted:" in preview.content and b"Questions:" in preview.content
        accepted = post(browser, path, {"action": "confirm", "preview": token(preview)})
    apply(store, accepted)
    current = SystemConfiguration.objects.get().active_configuration
    assert (
        current.content_versions.filter(kind="email", slot="confirmation").count() == 1
    )
    assert current.content_versions.get(slot="confirmation").subject == "After"
    assert before.content_versions.get(record_id=row["id"]).subject == "Before"
    assert browser.get(path + "/" + row["id"]).status_code == 404


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


def test_empty_content_editor_can_start_from_the_default(auth_service, google):
    """An empty slot's editor pre-fills its default without creating a request."""
    store = auth_service.store
    campaign, catalog, _ = setup(store)
    path = catalog + "/page/welcome"
    browser, _ = signed_in()
    requests = ConfigurationChangeRequest.objects.count()
    empty = browser.get(path)
    assert b"Start from the default text" in empty.content
    started = browser.get(path + "?start=default")
    assert started.status_code == 200
    assert b"Personal prayer" in started.content
    assert b"Start from the default text" not in started.content
    # The form posts to the clean path; a POST accepts no query string.
    assert f'<form method="post" action="{path}"'.encode() in started.content
    assert browser.get(path + "?start=other").status_code == 400
    assert browser.get(catalog + "?start=default").status_code == 400
    assert ConfigurationChangeRequest.objects.count() == requests
    email = browser.get(catalog + "/email/reminder?start=default")
    assert email.status_code == 200 and b"Complete your household" in email.content


def test_configured_content_editor_can_reset_to_the_default(auth_service, google):
    """A configured slot's reset only pre-fills; applying it replaces the text."""
    from parishkit.stewardship.accounts.content_defaults import default_data
    from parishkit.stewardship.accounts.content_forms import matches_default

    store = auth_service.store
    campaign, catalog, _ = setup(store)
    path = catalog + "/page/welcome"
    browser, _ = signed_in()
    browser.get(path)
    mine = post(browser, path, values(store, html="<p>Mine</p>"))
    apply(store, post(browser, path, {"action": "confirm", "preview": token(mine)}))
    listing = browser.get(catalog).content.decode()
    assert "Family welcome</a> — Customized" in listing
    saved = browser.get(path)
    assert b"Reset to the default text" in saved.content
    assert b"Start from the default text" not in saved.content
    requests = ConfigurationChangeRequest.objects.count()
    reset = browser.get(path + "?start=default")
    assert reset.status_code == 200
    assert b"Personal prayer" in reset.content
    assert b"will replace the current text" in reset.content
    assert b"Reset to the default text" not in reset.content
    assert ConfigurationChangeRequest.objects.count() == requests
    preview = post(
        browser,
        path,
        default_data("page", "welcome")
        | {"base_digest": store.active().digest, "action": "preview"},
    )
    assert preview.status_code == 200, preview.content
    apply(store, post(browser, path, {"action": "confirm", "preview": token(preview)}))
    record = next(
        row
        for row in store.active().document()["sections"]["content"]
        if (row["values"]["kind"], row["values"]["slot"]) == ("page", "welcome")
    )
    assert matches_default(record["values"])
    assert "Family welcome</a> — Default text" in browser.get(catalog).content.decode()


def test_removing_a_scheduled_template_explains_the_fix_inline(auth_service, google):
    """The refusal names the schedule problem and links Mail schedules on the form."""
    store = auth_service.store
    campaign, catalog, schedule = setup(store)
    row = content(str(campaign.pk), kind="email", slot="initial")
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {"operation": "add", "section": "content", **row},
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
    browser.get(path)
    requests = ConfigurationChangeRequest.objects.count()
    refused = post(browser, path, values(store, subject="Invitation", clear="on"))
    body = refused.content.decode()
    assert refused.status_code == 400
    assert "used by a mail schedule, so it can&#x27;t be removed" in body
    assert f'<a href="/admin/campaign/{campaign.pk}/schedules">' in body
    assert ConfigurationChangeRequest.objects.count() == requests


def test_stale_content_form_says_to_reload(auth_service, google):
    """An editor posted against an older configuration explains the reload."""
    store = auth_service.store
    _, catalog, _ = setup(store)
    browser, _ = signed_in()
    path = catalog + "/page/welcome"
    browser.get(path)
    stale = post(browser, path, values(store) | {"base_digest": "0" * 64})
    assert stale.status_code == 409
    assert "another tab" in stale.json()["refusal"]["message"]


def test_plain_text_preview_is_generated_by_the_server_for_admins(auth_service, google):
    """The editors' preview uses the server generator; nothing else is accepted."""
    browser, _ = signed_in()
    path = "/admin/content/plain-text"
    session = PortalSession.objects.get(revoked_at__isnull=True)
    response = post(
        browser, path, {"html": '<p>Hi <a href="https://example.org/">there</a></p>'}
    )
    assert response.status_code == 200, response.content
    assert response.json() == {
        "html": '<p>Hi <a href="https://example.org/" rel="noopener noreferrer">'
        "there</a></p>",
        "text": "Hi there: https://example.org/",
        "removed": [],
    }
    # The live visual editor redraws only from the sanitized HTML and reports
    # what was removed; the raw source is never echoed back.
    unsafe = post(
        browser, path, {"html": '<script>x()</script><p onclick="y()">Hello</p>'}
    ).json()
    assert unsafe == {
        "html": "<p>Hello</p>",
        "text": "Hello",
        "removed": ["<script> element and its content", "onclick attribute"],
    }
    # Oversized source is refused like any other bounded content.
    assert post(browser, path, {"html": "x" * (128 * 1024 + 1)}).status_code == 400
    # Live previews fire while an Admin types; they never renew the idle session.
    assert (
        PortalSession.objects.get(pk=session.pk).last_activity_at
        == session.last_activity_at
    )
    assert response["Cache-Control"] == "no-store"
    assert post(browser, path, {"html": "x", "other": "y"}).status_code == 400
    assert browser.get(path).status_code == 405
    # A signed-out browser (or a missing CSRF token) is refused.
    assert Client(enforce_csrf_checks=True).post(path, {"html": "x"}).status_code in {
        403,
        302,
    }


def test_confirmation_closing_note_is_listed_with_the_confirmation_email(
    auth_service, google
):
    """The receipt's closing note sits under Email templates, after the receipt."""
    _, catalog, _ = setup(auth_service.store)
    browser, _ = signed_in()
    body = browser.get(catalog).content.decode()
    pages, emails = body.split("Email templates", 1)
    assert "Confirmation email: closing note" not in pages
    assert emails.index("Submission receipt") < emails.index(
        "Confirmation email: closing note"
    )
    assert "/page/submission_confirmation" in emails
