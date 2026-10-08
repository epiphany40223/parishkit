"""Real-session content editing, sanitization, immutable revisions and mail impacts."""

from html import unescape
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
    return campaign, "/admin/campaign/content/", schedule


def values(store, **changes):
    """Submit HTML form values, not raw YAML patches or browser role declarations."""
    return fields(base_digest=store.active().digest, action="preview", **changes)


def test_page_content_apply_sanitizes_samples_and_replays(auth_service, google):
    """Preview emits sanitized fictional data; confirmation installs separately."""
    store = auth_service.store
    campaign, catalog, _ = setup(store)
    path = catalog + "page/welcome/"
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
        post(browser, path + "page/welcome/", values(store, **changes)).status_code
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
    path = catalog + "email/initial/" + row["id"] + "/"
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
    path = catalog + "email/reminder/"
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
    path = catalog + "page/login_help/"
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
    path = catalog + "page/welcome/"
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
    path = catalog + "page/welcome/"
    assert (
        post(browser, path, values(store) | {"base_digest": "f" * 64}).status_code
        == 409
    )
    assert post(browser, path, values(store, clear="on")).status_code == 400
    proposal = token(post(browser, path, values(store)))
    assert (
        post(
            browser,
            catalog + "page/review/",
            {"action": "confirm", "preview": proposal},
        ).status_code
        == 400
    )
    assert browser.get(catalog + "page/financial/").status_code == 404
    assert browser.get(catalog + "unknown/welcome/").status_code == 404
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
    path = catalog + "email/confirmation/"
    with task_login(ServiceRole.WEB):
        assert b"Edit confirmation email" in browser.get(catalog).content
        assert b"Before" in browser.get(path).content
        preview = post(browser, path, values(store, subject="After"))
        assert b"Submitted:" in preview.content and b"Questions:" not in preview.content
        accepted = post(browser, path, {"action": "confirm", "preview": token(preview)})
    apply(store, accepted)
    current = SystemConfiguration.objects.get().active_configuration
    assert (
        current.content_versions.filter(kind="email", slot="confirmation").count() == 1
    )
    assert current.content_versions.get(slot="confirmation").subject == "After"
    assert before.content_versions.get(record_id=row["id"]).subject == "Before"
    assert browser.get(path + row["id"] + "/").status_code == 404


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
    assert post(browser, catalog + "page/welcome/", values(store)).status_code == 403


def test_empty_content_editor_can_start_from_the_default(auth_service, google):
    """An empty slot's editor pre-fills its default without creating a request."""
    store = auth_service.store
    campaign, catalog, _ = setup(store)
    path = catalog + "page/welcome/"
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
    email = browser.get(catalog + "email/reminder/?start=default")
    assert email.status_code == 200 and b"Complete your household" in email.content


def test_configured_content_editor_can_reset_to_the_default(auth_service, google):
    """A configured slot's reset only pre-fills; applying it replaces the text."""
    from parishkit.stewardship.accounts.content_defaults import default_data
    from parishkit.stewardship.accounts.content_forms import matches_default

    store = auth_service.store
    campaign, catalog, _ = setup(store)
    path = catalog + "page/welcome/"
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
    """The refusal names the schedule problem and links its page on the form."""
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
    path = catalog + "email/initial/" + row["id"] + "/"
    browser.get(path)
    requests = ConfigurationChangeRequest.objects.count()
    refused = post(browser, path, values(store, subject="Invitation", clear="on"))
    body = refused.content.decode()
    assert refused.status_code == 400
    assert "used by a mail schedule, so it can&#x27;t be removed" in body
    assert '<a href="/admin/campaign/schedules/">' in body
    assert ConfigurationChangeRequest.objects.count() == requests


def test_stale_content_form_says_to_reload(auth_service, google):
    """An editor posted against an older configuration explains the reload."""
    store = auth_service.store
    _, catalog, _ = setup(store)
    browser, _ = signed_in()
    path = catalog + "page/welcome/"
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


def test_confirmation_editor_folds_a_retired_closing_note(
    auth_service, google, monkeypatch
):
    """An applied closing note (#260) opens inside the email; saving keeps it."""
    from parishkit.stewardship.accounts import content_schema

    store = auth_service.store
    campaign, catalog, _ = setup(store)
    email = content(str(campaign.pk), kind="email", slot="confirmation")
    note = content(
        str(campaign.pk),
        slot="submission_confirmation",
        html="<p>Call the office.</p>",
        text="Call the office.",
    )
    # Plant the note as a configuration applied before #260 carried it.
    with monkeypatch.context() as patched:
        patched.setattr(content_schema, "RETIRED", None)
        assert (
            change(
                store,
                store.active(),
                uuid4(),
                [
                    {"operation": "add", "section": "content", **email},
                    {"operation": "add", "section": "content", **note},
                ],
            ).state
            == "applied"
        )
    browser, _ = signed_in()
    body = browser.get(catalog).content.decode()
    assert "closing note" not in body and "submission_confirmation" not in body
    assert browser.get(catalog + "page/submission_confirmation/").status_code != 200
    path = catalog + "email/confirmation/"
    assert "Call the office." in browser.get(path).content.decode()
    # Saving the email exactly as shown folds the note into its body.
    html = email["values"]["html"] + note["values"]["html"]
    preview = post(
        browser,
        path,
        values(store, subject=email["values"]["subject"], html=html),
    )
    assert preview.status_code == 200
    # "Before" (email plus note) and "after" (folded email) each say it once.
    assert preview.content.decode().count("Call the office.") == 4
    apply(store, post(browser, path, {"action": "confirm", "preview": token(preview)}))
    rows = SystemConfiguration.objects.get().active_configuration.content_versions
    assert not rows.filter(slot="submission_confirmation").exists()
    folded = rows.get(kind="email", slot="confirmation")
    assert folded.html == html
    assert folded.text == "Welcome to {{ parish_name }}.\n\nCall the office."


def plant(store, monkeypatch, *records):
    """Apply records as a configuration from before #260 could carry them."""
    from parishkit.stewardship.accounts import content_schema

    with monkeypatch.context() as patched:
        patched.setattr(content_schema, "RETIRED", None)
        patch = [{"operation": "add", "section": "content", **row} for row in records]
        assert change(store, store.active(), uuid4(), patch).state == "applied"


def closing_note(campaign):
    """One retired receipt closing note with distinctive text."""
    return content(
        str(campaign.pk),
        slot="submission_confirmation",
        html="<p>Call the office.</p>",
        text="Call the office.",
    )


def test_confirmation_reset_to_default_removes_a_retired_closing_note(
    auth_service, google, monkeypatch
):
    """Resetting shows the note only in "before" and removes it on apply."""
    from parishkit.stewardship.accounts.content_defaults import default_data
    from parishkit.stewardship.accounts.content_forms import matches_default

    store = auth_service.store
    campaign, catalog, _ = setup(store)
    email = content(str(campaign.pk), kind="email", slot="confirmation")
    plant(store, monkeypatch, email, closing_note(campaign))
    browser, _ = signed_in()
    path = catalog + "email/confirmation/"
    editor = browser.get(path + "?start=default").content.decode()
    assert "Call the office." not in editor
    default = default_data("email", "confirmation")
    preview = post(
        browser,
        path,
        values(store, subject=default["subject"], html=default["html"]),
    )
    assert preview.status_code == 200
    # Only "before" (today's receipt, HTML and text) still carries the note.
    assert preview.content.decode().count("Call the office.") == 2
    apply(store, post(browser, path, {"action": "confirm", "preview": token(preview)}))
    rows = SystemConfiguration.objects.get().active_configuration.content_versions
    assert not rows.filter(slot="submission_confirmation").exists()
    saved = rows.get(kind="email", slot="confirmation")
    assert matches_default(
        {
            "campaign_id": str(campaign.pk),
            "kind": "email",
            "slot": "confirmation",
            "subject": saved.subject,
            "html": saved.html,
            "text": saved.text,
        }
    )


def test_closing_note_without_confirmation_email_is_listed_and_folded(
    auth_service, google, monkeypatch
):
    """A note alone shows as the built-in email plus note, and folds on save."""
    from parishkit.stewardship.jobs.receipt_content import ReceiptTemplate

    store = auth_service.store
    campaign, catalog, _ = setup(store)
    plant(store, monkeypatch, closing_note(campaign))
    browser, _ = signed_in()
    body = browser.get(catalog).content.decode()
    confirmation = body.split(">Confirmation email<", 1)[1].split("<h3>", 1)[0]
    fallback = ReceiptTemplate()
    assert fallback.subject in confirmation
    assert "No template yet." not in confirmation
    path = catalog + "email/confirmation/"
    editor = browser.get(path)
    assert "Call the office." in editor.content.decode()
    # Saved text is offered a reset, not a fresh start from the default.
    assert editor.context["saved"]
    html = fallback.html + "<p>Call the office.</p>"
    preview = post(browser, path, values(store, subject=fallback.subject, html=html))
    assert preview.status_code == 200
    apply(store, post(browser, path, {"action": "confirm", "preview": token(preview)}))
    rows = SystemConfiguration.objects.get().active_configuration.content_versions
    assert not rows.filter(slot="submission_confirmation").exists()
    saved = rows.get(kind="email", slot="confirmation")
    assert (saved.subject, saved.html) == (fallback.subject, html)


def plant_stale(store, monkeypatch, *records):
    """Apply records as if saved before a sanitizer change (#832).

    Today's content rules refuse non-canonical HTML only for authored records,
    so patching them off for one change stands in for an older sanitizer.
    """
    from parishkit.stewardship.accounts import content_schema
    from parishkit.stewardship.web.content import SafeContent

    with monkeypatch.context() as patched:
        patched.setattr(
            content_schema,
            "prepare_content",
            lambda html, text=None: SafeContent(html, text),
        )
        patch = [{"operation": "add", "section": "content", **row} for row in records]
        assert change(store, store.active(), uuid4(), patch).state == "applied"


def test_stale_saved_content_is_flagged_and_resaves_without_an_edit(
    auth_service, google, monkeypatch
):
    """The list and editor say re-save; previewing the unchanged form cleans it."""
    store = auth_service.store
    campaign, catalog, _ = setup(store)
    stale = "<p>Welcome to {{ parish_name }}.</p><!-- old note -->"
    page = content(str(campaign.pk), html=stale)
    email = content(
        str(campaign.pk),
        kind="email",
        slot="initial",
        html='<p onclick="x()">Hi {{ family_code }} {{ family_url }}</p>',
        text="Hi {{ family_code }} {{ family_url }}",
    )
    plant_stale(store, monkeypatch, page, email)
    browser, _ = signed_in()
    listing = browser.get(catalog).content.decode()
    pages, emails = listing.split("<h2>Email templates</h2>", 1)
    welcome = pages.split(">Family welcome</a>", 1)[1].split("</li>", 1)[0]
    assert "Re-save recommended" in welcome
    assert pages.count("Re-save recommended") == 1
    assert emails.count("Re-save recommended") == 1
    path = catalog + "page/welcome/"
    editor = browser.get(path)
    assert editor.context["stale"].removed == ("HTML comments",)
    assert "markup that sending already removes: HTML comments." in (
        editor.content.decode()
    )
    email_editor = browser.get(catalog + "email/initial/" + email["id"] + "/")
    assert email_editor.context["stale"].removed == ("onclick attribute",)
    # Posting the saved text unchanged is enough: the form stores it cleaned.
    preview = post(browser, path, values(store, html=stale))
    assert preview.status_code == 200
    apply(store, post(browser, path, {"action": "confirm", "preview": token(preview)}))
    rows = SystemConfiguration.objects.get().active_configuration.content_versions
    assert rows.get(kind="page", slot="welcome").html == (
        "<p>Welcome to {{ parish_name }}.</p>"
    )
    assert browser.get(path).context["stale"] is None
    pages = browser.get(catalog).content.decode().split("<h2>Email templates", 1)[0]
    assert "Re-save recommended" not in pages


def test_clean_saved_content_is_not_flagged(auth_service, google, monkeypatch):
    """Content already in today's cleaned form shows no re-save notice."""
    store = auth_service.store
    campaign, catalog, _ = setup(store)
    plant(store, monkeypatch, content(str(campaign.pk)))
    browser, _ = signed_in()
    assert "Re-save recommended" not in browser.get(catalog).content.decode()
    editor = browser.get(catalog + "page/welcome/")
    assert editor.context["stale"] is None
    assert b"data-resave-notice" not in editor.content
    # A default being started is not the saved text, so it is never flagged.
    assert browser.get(catalog + "page/welcome/?start=default").context["stale"] is None


def test_family_email_that_lost_its_code_is_flagged_and_refused(
    auth_service, google, monkeypatch
):
    """A code only in removed markup can't be sent; the re-save says why."""
    store = auth_service.store
    campaign, catalog, _ = setup(store)
    stale = "<p>Open {{ family_url }}</p><!-- {{ family_code }} -->"
    email = content(
        str(campaign.pk),
        kind="email",
        slot="initial",
        html=stale,
        text="Open {{ family_url }} {{ family_code }}",
    )
    plant_stale(store, monkeypatch, email)
    browser, _ = signed_in()
    listing = browser.get(catalog).content.decode()
    assert "Can't be sent until fixed" in listing
    assert "Re-save recommended" not in listing
    path = catalog + "email/initial/" + email["id"] + "/"
    editor = browser.get(path)
    assert editor.context["stale"].blocked
    body = editor.content.decode()
    assert "{{ family_code }} is only inside that markup" in body
    assert "same content either way" not in body
    # Re-saving unchanged is refused, and the notice stays to explain why.
    refused = post(
        browser,
        path,
        values(
            store,
            subject=email["values"]["subject"],
            html=stale,
            generate_text="",
            text=email["values"]["text"],
        ),
    )
    assert refused.status_code == 400
    assert "{{ family_code }}" in refused.content.decode()
    assert refused.context["stale"].blocked


def test_folded_confirmation_with_a_stale_note_resaves_clean(
    auth_service, google, monkeypatch
):
    """The list and editor check the folded receipt; a re-save drops the note."""
    store = auth_service.store
    campaign, catalog, _ = setup(store)
    email = content(
        str(campaign.pk),
        kind="email",
        slot="confirmation",
        html="<p>Thanks.</p>",
        text="Thanks.",
    )
    note = content(
        str(campaign.pk),
        slot="submission_confirmation",
        html="<p>Call.</p><!-- x -->",
        text="Call.",
    )
    with monkeypatch.context() as patched:
        from parishkit.stewardship.accounts import content_schema

        patched.setattr(content_schema, "RETIRED", None)
        plant_stale(store, patched, email, note)
    browser, _ = signed_in()
    confirmation = (
        browser.get(catalog)
        .content.decode()
        .split(">Confirmation email<", 1)[1]
        .split("<h3>", 1)[0]
    )
    assert "Re-save recommended" in confirmation
    path = catalog + "email/confirmation/"
    editor = browser.get(path)
    assert editor.context["stale"].removed == ("HTML comments",)
    preview = post(
        browser,
        path,
        values(
            store,
            subject=email["values"]["subject"],
            html=editor.context["form"]["html"].value(),
        ),
    )
    assert preview.status_code == 200
    apply(store, post(browser, path, {"action": "confirm", "preview": token(preview)}))
    rows = SystemConfiguration.objects.get().active_configuration.content_versions
    assert not rows.filter(slot="submission_confirmation").exists()
    assert rows.get(kind="email", slot="confirmation").html == (
        "<p>Thanks.</p><p>Call.</p>"
    )
    assert browser.get(path).context["stale"] is None


def test_catalog_lists_who_sends_each_email_and_offers_remove_when_unused(
    auth_service, google
):
    """The invitation sent by a schedule names it; an unused one offers Remove.

    Two invitation emails share a subject, so each also shows the start of
    its ID (#446).
    """
    store = auth_service.store
    campaign, catalog, schedule = setup(store)
    used = content(str(campaign.pk), kind="email", slot="initial", subject="Same")
    spare = content(str(campaign.pk), kind="email", slot="initial", subject="Same")
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [
                {"operation": "add", "section": "content", **used},
                {"operation": "add", "section": "content", **spare},
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": schedule["id"],
                    "values": {"template_version": used["id"], "subject": "Same"},
                },
            ],
        ).state
        == "applied"
    )
    browser, _ = signed_in()
    page = unescape(browser.get(catalog).content.decode())
    rows = {
        identifier: page[page.index(f"/email/initial/{identifier}/") :][:2000]
        for identifier in (used["id"], spare["id"])
    }
    assert f"({used['id'][:8]})" in page and f"({spare['id'][:8]})" in page
    assert "Initial invitation" in rows[used["id"]].split("</tr>")[0]
    assert "#id_clear" not in rows[used["id"]].split("</tr>")[0]
    spare_row = rows[spare["id"]].split("</tr>")[0]
    assert "No schedule" in spare_row
    assert f"/email/initial/{spare['id']}/#id_clear" in spare_row
