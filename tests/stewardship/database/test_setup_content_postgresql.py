"""Private initial content editing uses real source evidence and restricted SQL."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from uuid import uuid4

import pytest
from django.db import DatabaseError, transaction
from django.test import Client

from parishkit.stewardship.accounts.content_defaults import EMAILS, default_data
from parishkit.stewardship.accounts.content_forms import matches_default, page_slots
from parishkit.stewardship.accounts.setup_campaign import campaign_catalog
from parishkit.stewardship.accounts.setup_drafts import save_section
from parishkit.stewardship.accounts.setup_models import SetupAttempt, SetupDraftSection
from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction

from ..campaign_factory import campaign
from ..content_factory import content
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_campaign_postgresql import completed
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401
from .test_setup_views_postgresql import post, setup_http  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def first_campaign(service, monkeypatch, **overrides):
    """Use the original load result and web-authorized campaign staging."""
    request, attempt = completed(service, monkeypatch)
    with web_login():
        catalog = campaign_catalog(request, service, attempt.pk)
        saved = save_section(
            request,
            service,
            attempt.pk,
            step="campaign",
            values={
                "source_result": str(catalog.result_id),
                "campaign": campaign(timezone=catalog.timezone, **overrides)["values"],
            },
            expected_version=attempt.version,
        )
    return request, saved


def test_content_is_private_temporary_versioned_and_scrubbed(
    setup_service, monkeypatch
):
    """A large canonical page fits staging and never creates an active campaign."""
    request, attempt = first_campaign(setup_service, monkeypatch)
    record = content(
        str(attempt.attempt_id), html="<p>" + "x" * 70000 + "</p>", text="x" * 70000
    )
    with web_login():
        saved = save_section(
            request,
            setup_service,
            attempt.attempt_id,
            step="page_welcome",
            values=record,
            expected_version=attempt.version,
        )
        assert SetupDraftSection.objects.get(step="page_welcome").values == record
        assert saved.version == attempt.version + 1
        with pytest.raises(LookupError):
            save_section(
                login(setup_service),
                setup_service,
                attempt.attempt_id,
                step="page_welcome",
                values=record,
                expected_version=saved.version,
            )
        cancel_setup(request, setup_service, attempt.attempt_id)
        assert SetupDraftSection.objects.get(step="page_welcome").values == {}
    assert not Campaign.objects.exists() and not setup_service.configured()


@pytest.mark.parametrize("invalid", ["campaign", "disabled"])
def test_content_rechecks_campaign_and_enabled_slot(
    setup_service, monkeypatch, invalid
):
    """Hidden controls and foreign IDs cannot create unrelated wizard content."""
    request, attempt = first_campaign(setup_service, monkeypatch)
    slot = "financial" if invalid == "disabled" else "welcome"
    record = content(
        str(uuid4()) if invalid == "campaign" else str(attempt.attempt_id), slot=slot
    )
    with web_login(), pytest.raises((ValueError, LookupError)):
        save_section(
            request,
            setup_service,
            attempt.attempt_id,
            step=f"page_{slot}",
            values=record,
            expected_version=attempt.version,
        )
    assert not SetupDraftSection.objects.filter(step=f"page_{slot}").exists()


def test_sql_rejects_foreign_campaign_even_without_service(setup_service, monkeypatch):
    """The worker-independent web guard repeats attempt and content ownership."""
    request, attempt = first_campaign(setup_service, monkeypatch)
    with (
        web_login(),
        pytest.raises(DatabaseError, match="original campaign and slot"),
        transaction.atomic(),
        work_transaction(),
    ):
        SetupDraftSection.objects.create(
            attempt_id=attempt.attempt_id,
            step="page_welcome",
            values=content(str(uuid4())),
            actor_id=request.portal_session.principal_id,
        )


def test_content_http_csrf_preview_and_clear(setup_http, monkeypatch):
    """The actual editor sanitizes invalid input and renders only fictional data."""
    request, attempt = first_campaign(setup_http, monkeypatch)
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    url = "/admin/setup/content/page/welcome"
    with web_login():
        listing = browser.get("/admin/setup/content")
        assert listing.status_code == 200
        # The receipt's closing note is listed with the confirmation email.
        pages, emails = listing.content.decode().split("Email templates", 1)
        assert "Confirmation email: closing note" not in pages
        assert emails.index("Submission receipt") < emails.index(
            "Confirmation email: closing note"
        )
        response = browser.get(url)
        assert response.status_code == 200, response.content
        assert response["Cache-Control"] == "no-store"
        # Web-only pages have no plain-text controls (#259), so the editor
        # posts only the HTML; the plain text is always generated.
        data = {
            "version": str(attempt.version),
            "html": "<p>Hello {{ family_name }}</p>",
        }
        assert browser.post(url, data).status_code == 403
        invalid = post(
            browser, url, data | {"html": '<p onclick="unsafe()">{{ invalid }}</p>'}
        )
        assert invalid.status_code == 400
        assert b'<p onclick="unsafe()">' not in invalid.content
        unexpected = post(browser, url, data | {"unrecognized": "value"})
        assert unexpected.status_code == 400
        assert unexpected.json()["refusal"]["message"] == (
            "The form was submitted with unexpected data."
        )
        saved = post(browser, url, data)
        assert saved.status_code == 302, saved.content
        assert saved["Location"] == "/admin/setup/content"
        stale = post(browser, url, data)
        assert stale.status_code == 409
        assert "another tab" in stale.json()["refusal"]["message"]
        preview = browser.get(url)
        assert b"<p>Hello Sample</p>" in preview.content
        assert b"Save and return to the content list" in preview.content
        assert (
            post(
                browser, url, {"version": str(attempt.version + 1), "clear": "on"}
            ).status_code
            == 302
        )
        assert SetupDraftSection.objects.get(step="page_welcome").values == {
            "id": None,
            "values": None,
        }
        assert browser.get("/admin/setup/content/page/financial").status_code == 404
    assert not setup_http.configured()


def content_browser(service, monkeypatch):
    """A CSRF-enforcing browser signed in as the original setup Admin."""
    request, attempt = first_campaign(service, monkeypatch)
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    return request, attempt, browser


def content_rows():
    """Current staged content values by step, excluding other wizard steps."""
    return {
        row.step: row.values
        for row in SetupDraftSection.objects.filter(scrubbed_at=None)
        if row.step.startswith(("page_", "email_"))
    }


def test_fill_defaults_fills_only_empty_slots_in_one_version(setup_http, monkeypatch):
    """One POST saves every empty applicable slot; saved text is never replaced."""
    request, attempt, browser = content_browser(setup_http, monkeypatch)
    url = "/admin/setup/content"
    with web_login():
        page = browser.get(url)
        assert b"Fill in the default text for all pages and emails" in page.content
        mine = {"version": str(attempt.version), "html": "<p>Mine</p>"}
        assert post(browser, url + "/page/welcome", mine).status_code == 302
        kept = content_rows()["page_welcome"]
        data = {"version": str(attempt.version + 1)}
        assert browser.post(url, data).status_code == 403
        filled = post(browser, url, data)
        assert filled.status_code == 302, filled.content
        slots = page_slots(
            SetupDraftSection.objects.get(step="campaign").values["campaign"]
        )
        assert filled["Location"] == (
            f"{url}?filled_pages={len(slots) - 1}&filled_emails={len(EMAILS)}"
        )
        rows = content_rows()
        assert rows["page_welcome"] == kept
        assert set(rows) == {f"page_{slot}" for slot in slots} | {
            f"email_{slot}" for slot in EMAILS
        }
        assert "page_financial" not in rows  # Disabled modules stay empty.
        initial = rows["email_initial"]["values"]
        assert "{{ family_url }}" in initial["html"] and initial["text"]
        assert SetupAttempt.objects.get().version == attempt.version + 2
        report = browser.get(filled["Location"])
        assert b"Filled in the default text for" in report.content
        assert b"Fill in the default text for all" not in report.content
        # Nothing left to fill: a repeated POST saves nothing.
        again = post(browser, url, {"version": str(attempt.version + 2)})
        assert again["Location"].endswith("filled_pages=0&filled_emails=0")
        assert SetupAttempt.objects.get().version == attempt.version + 2
        assert browser.get(url + "?filled_pages=x").status_code == 400
    assert not setup_http.configured()


def test_fill_defaults_is_stale_after_another_edit(setup_http, monkeypatch):
    """An older page version cannot fill slots after a concurrent change."""
    request, attempt, browser = content_browser(setup_http, monkeypatch)
    with web_login():
        assert browser.get("/admin/setup/content").status_code == 200
        data = {"version": str(attempt.version), "html": "<p>Other tab</p>"}
        assert (
            post(browser, "/admin/setup/content/page/review", data).status_code == 302
        )
        stale = post(browser, "/admin/setup/content", {"version": str(attempt.version)})
        assert stale.status_code == 409
        assert set(content_rows()) == {"page_review"}


def test_fill_defaults_refuses_when_setup_is_not_collecting(setup_http, monkeypatch):
    """A cancelled attempt cannot be refilled with temporary content."""
    request, attempt, browser = content_browser(setup_http, monkeypatch)
    with web_login():
        assert browser.get("/admin/setup/content").status_code == 200
        cancel_setup(request, setup_http, attempt.attempt_id)
        refused = post(
            browser, "/admin/setup/content", {"version": str(attempt.version + 1)}
        )
        assert refused.status_code >= 400
        assert not any(values for values in content_rows().values())


def test_start_from_default_prefills_without_saving(setup_http, monkeypatch):
    """An empty editor can start from its default; nothing is staged until saved."""
    request, attempt, browser = content_browser(setup_http, monkeypatch)
    url = "/admin/setup/content/email/initial"
    with web_login():
        empty = browser.get(url)
        assert b"Start from the default text" in empty.content
        assert b"Reset to the default text" not in empty.content
        started = browser.get(url + "?start=default")
        assert started.status_code == 200, started.content
        assert b"not saved yet" in started.content
        assert b"Saving will replace" not in started.content
        assert b"Begin your household" in started.content
        assert b"Start from the default text" not in started.content
        # The form posts to the clean path; a POST accepts no query string.
        assert f'<form method="post" action="{url}"'.encode() in started.content
        assert not content_rows()
        assert browser.get(url + "?start=other").status_code == 400
        assert browser.get(url + "?start=default&x=1").status_code == 400
        assert post(browser, url + "?start=default", {}).status_code == 400


def test_reset_to_default_prefills_a_saved_slot_until_saved(setup_http, monkeypatch):
    """A saved slot offers a reset that replaces its text only on a normal save."""
    request, attempt, browser = content_browser(setup_http, monkeypatch)
    url = "/admin/setup/content/email/initial"
    data = {
        "version": str(attempt.version),
        "subject": "Invitation",
        "html": '<p><a href="{{ family_url }}">Go</a> {{ family_code }}</p>',
        "text": "{{ family_url }} {{ family_code }}",
    }
    with web_login():
        browser.get(url)
        assert post(browser, url, data).status_code == 302
        mine = content_rows()["email_initial"]
        saved = browser.get(url)
        assert b"Reset to the default text" in saved.content
        assert b"Start from the default text" not in saved.content
        reset = browser.get(url + "?start=default")
        assert reset.status_code == 200, reset.content
        assert b"Saving will replace the current text" in reset.content
        assert b"Begin your household" in reset.content
        assert b"Reset to the default text" not in reset.content
        # Viewing the reset form changed nothing.
        assert content_rows()["email_initial"] == mine
        assert SetupAttempt.objects.get().version == attempt.version + 1
        # Saving the pre-filled default replaces the text.
        default = default_data("email", "initial")
        replaced = post(browser, url, {"version": str(attempt.version + 1)} | default)
        assert replaced.status_code == 302, replaced.content
        values = content_rows()["email_initial"]["values"]
        assert matches_default(values) and values != mine["values"]


def test_reset_all_replaces_every_slot_only_when_confirmed(setup_http, monkeypatch):
    """The confirmed reset replaces saved text; schedules follow the new revision."""
    request, attempt, browser = content_browser(setup_http, monkeypatch)
    url = "/admin/setup/content"
    with web_login():
        page = browser.get(url)
        assert b"Reset all pages and emails to the default text" in page.content
        mine = {"version": str(attempt.version), "html": "<p>Mine</p>"}
        assert post(browser, url + "/page/welcome", mine).status_code == 302
        version = str(attempt.version + 1)
        for data in (
            {"version": version, "reset": "on"},
            {"version": version, "confirm": "on"},
            {"version": version, "reset": "yes", "confirm": "on"},
        ):
            refused = post(browser, url, data)
            assert refused.status_code == 400, data
        assert "confirmation box" in refused.json()["refusal"]["message"]
        assert set(content_rows()) == {"page_welcome"}
        done = post(browser, url, {"version": version, "reset": "on", "confirm": "on"})
        assert done.status_code == 302, done.content
        rows = content_rows()
        slots = page_slots(
            SetupDraftSection.objects.get(step="campaign").values["campaign"]
        )
        assert done["Location"] == (
            f"{url}?reset_pages={len(slots)}&reset_emails={len(EMAILS)}"
        )
        assert all(matches_default(row["values"]) for row in rows.values())
        report = browser.get(done["Location"])
        assert b"Reset 12 page(s) and 6 email(s)" in report.content
        # Everything already matches its default: a repeat replaces nothing.
        again = post(
            browser,
            url,
            {"version": str(attempt.version + 2), "reset": "on", "confirm": "on"},
        )
        assert again["Location"].endswith("reset_pages=0&reset_emails=0")
        assert content_rows() == rows


def test_fill_result_names_kept_customized_slots_and_badges(setup_http, monkeypatch):
    """The fill report links every slot it kept; the list marks default vs custom."""
    request, attempt, browser = content_browser(setup_http, monkeypatch)
    url = "/admin/setup/content"
    with web_login():
        browser.get(url)
        mine = {"version": str(attempt.version), "html": "<p>Mine</p>"}
        assert post(browser, url + "/page/welcome", mine).status_code == 302
        filled = post(browser, url, {"version": str(attempt.version + 1)})
        report = browser.get(filled["Location"]).content.decode()
        assert "Kept your saved text for:" in report
        assert (
            '<a href="/admin/setup/content/page/welcome">Family welcome</a>' in report
        )
        assert "Reset to the default text" in report
        assert report.count("— Customized") == 1
        assert report.count("— Default text") == 12 + 6 - 1
        assert "— Empty" not in report
        # Without a fill result, the list shows only the badges.
        plain = browser.get(url).content.decode()
        assert "Kept your saved text" not in plain


def campaign_fields(values, version):
    """First-campaign form data for ``values``, as a browser would post it."""
    from parishkit.stewardship.accounts.campaign_forms import initial_fields

    fields = initial_fields(values, digest="")
    fields.pop("base_digest")
    return {
        key: "on" if value is True else value
        for key, value in fields.items()
        if value is not False and value is not None
    } | {"version": str(version)}


def test_saving_the_first_campaign_fills_every_applicable_slot(setup_http, monkeypatch):
    """The campaign save fills defaults atomically, then only new applicable slots."""
    request, attempt = completed(setup_http, monkeypatch)
    browser = Client(enforce_csrf_checks=True)
    browser.cookies["pk_admin"] = request.session.session_key
    url = "/admin/setup/campaign"
    with web_login():
        timezone = campaign_catalog(request, setup_http, attempt.pk).timezone
        values = campaign(timezone=timezone, additional_information=False)["values"]
        browser.get(url)
        saved = post(browser, url, campaign_fields(values, attempt.version))
        assert saved.status_code == 302, saved.content
        # Census only, no additional-information prompt: 10 pages, 6 emails.
        rows = content_rows()
        pages = {step for step in rows if step.startswith("page_")}
        assert len(pages) == 11 and "page_additional" not in pages
        assert len(rows) - len(pages) == len(EMAILS)
        assert all(matches_default(row["values"]) for row in rows.values())
        for row in rows.values():
            assert row["values"]["campaign_id"] == str(attempt.pk)
        # One version bump covers the campaign and all of its content.
        assert SetupAttempt.objects.get().version == attempt.version + 1
        assert saved["Location"] == (
            "/admin/setup/content?filled_pages=11&filled_emails=6"
        )
        listing = browser.get(saved["Location"])
        assert b"Filled in the default text for 11 page(s)" in listing.content
        assert b"filled in automatically" in listing.content
        assert b"Fill in the default text for all" not in listing.content

        # The Admin edits one slot and explicitly clears another.
        version = attempt.version + 1
        edit = "/admin/setup/content/page/"
        mine = {"version": str(version), "html": "<p>Mine</p>"}
        assert post(browser, edit + "welcome", mine).status_code == 302
        kept = content_rows()["page_welcome"]
        cleared = {"version": str(version + 1), "clear": "on"}
        assert post(browser, edit + "review", cleared).status_code == 302
        # Enabling the additional-information prompt fills only that new
        # slot; the edit and the explicit clear are both kept.
        values["additional_information"] = True
        again = post(browser, url, campaign_fields(values, version + 2))
        assert again.status_code == 302, again.content
        assert again["Location"] == (
            "/admin/setup/content?filled_pages=1&filled_emails=0"
        )
        rows = content_rows()
        assert matches_default(rows["page_additional"]["values"])
        assert rows["page_welcome"] == kept
        assert rows["page_review"] == {"id": None, "values": None}
        # The fill button refills the cleared slot; the edit still stays.
        button = browser.get("/admin/setup/content")
        assert b"Fill in the default text for all" in button.content
        filled = post(browser, "/admin/setup/content", {"version": str(version + 3)})
        assert filled["Location"].endswith("filled_pages=1&filled_emails=0")
        assert matches_default(content_rows()["page_review"]["values"])
        assert content_rows()["page_welcome"] == kept
        # A save that makes nothing newly applicable fills nothing.
        same = post(browser, url, campaign_fields(values, version + 4))
        assert same.status_code == 302
        assert "filled_" not in same["Location"]
    assert not setup_http.configured()
