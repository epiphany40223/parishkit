"""Wizard-only chrome, ordered Save-and-continue, stepper state and friendly pages."""

# ruff: noqa: F811 -- pytest resolves the imported fixture dependencies.

import pytest

from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_secret_models import SetupSealedCredential

from ..test_setup_forms import VALUES
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_credentials_postgresql import CANDIDATE, publish
from .test_setup_progress_postgresql import bind_load
from .test_setup_views_postgresql import post, setup_http, started  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def save(browser, step, values):
    """Submit one public step at the attempt's current version."""
    version = str(SetupAttempt.objects.get().version)
    return post(browser, "/admin/setup/" + step, values | {"version": version})


def test_before_completion_admin_pages_route_to_the_wizard_only(setup_http, google):
    """Other Admin pages redirect, and the chrome offers no other destination."""
    with web_login():
        browser = started()
        for path in ("/admin/", "/admin/configuration/integrations", "/admin/users"):
            response = browser.get(path)
            assert response.status_code == 302, path
            assert response["Location"] == "/admin/setup"
        body = browser.get("/admin/setup").content
        assert b"Initial setup" in body
        for other in (b"Parish settings", b'href="/admin/users"', b"Ministry activity"):
            assert other not in body
        assert b"Sign out" in body


def test_background_work_stays_readable_during_setup(setup_http, google):
    """The header's background link shows the setup load instead of bouncing."""
    browser = started()
    task, _ = bind_load(SetupAttempt.objects.get().pk)
    with web_login():
        body = browser.get("/admin/setup").content
        assert b'href="/admin/background"' in body
        page = browser.get("/admin/background")
        assert page.status_code == 200, page.content
        assert b"Initial ParishSoft data load" in page.content
        assert f"/admin/setup/source/{task.run_id}".encode() in page.content
        detail = browser.get(f"/admin/background/task/{task.run_id}")
        assert detail.status_code == 200
        assert b"Initial ParishSoft data load" in detail.content
        assert browser.get("/admin/background/counts").status_code == 200
        # Commands under the same prefix remain closed until setup completes.
        command = post(
            browser, f"/admin/background/tasks/{task.run_id}/retry-export-cleanup", {}
        )
        assert command.status_code == 503


def test_save_and_continue_follows_the_one_ordered_list(setup_http, google):
    """Each save goes to the next applicable page, including optional ones."""
    with web_login():
        browser = started()
        assert save(browser, "mail", VALUES["mail"])["Location"] == (
            "/admin/setup/testing"
        )
        assert save(browser, "testing", VALUES["testing"])["Location"] == (
            "/admin/setup/credentials/google_workspace"
        )
        slack = {"enabled": "on", "channel_id": "CEXAMPLE"}
        assert save(browser, "slack", slack)["Location"] == (
            "/admin/setup/credentials/slack"
        )
        disabled = {"channel_id": ""}
        assert save(browser, "slack", disabled)["Location"] == "/admin/setup/parish"
        assert save(browser, "parish", VALUES["parish"])["Location"] == (
            "/admin/setup/source"
        )


def test_stepper_marks_current_done_and_unavailable_steps(setup_http, google):
    """The ordered stepper names states in text and links only open pages."""
    with web_login():
        browser = started()
        save(browser, "mail", VALUES["mail"])
        page = browser.get("/admin/setup/testing")
        assert page.status_code == 200
        body = page.content
        assert body.count(b'aria-current="step"') == 1
        assert b'<ol class="setup-steps">' in body
        assert b"Completed" in body and b"Not available yet" in body
        assert b'href="/admin/setup/source"' not in body
        assert b'href="/admin/setup/mail"' in body  # Back
        assert b"Save and continue" in body
        assert b"stewardship/setup-v1.css" in body


def test_unavailable_wizard_pages_explain_the_missing_step_in_html(setup_http, google):
    """A browser GET explains the prerequisite; POSTs keep the closed JSON."""
    with web_login():
        browser = started()
        for path, reason, fix in (
            (
                "/admin/setup/campaign",
                b"Load parish data first",
                b"/admin/setup/source",
            ),
            (
                "/admin/setup/schedules",
                b"Save the first campaign",
                b"/admin/setup/campaign",
            ),
            (
                "/admin/setup/content",
                b"Save the first campaign",
                b"/admin/setup/campaign",
            ),
        ):
            response = browser.get(path)
            assert response.status_code == 404, path
            assert response["Content-Type"].startswith("text/html")
            assert response["Cache-Control"] == "no-store"
            assert reason in response.content and b'href="' + fix in response.content
        preview = browser.get("/admin/setup/preview")
        assert preview.status_code == 503
        assert b"Complete the required steps first" in preview.content
        posted = post(browser, "/admin/setup/campaign", {"version": "1"})
        assert posted.status_code == 404
        assert posted["Content-Type"] == "application/json"


def test_source_step_offers_the_load_only_after_its_prerequisites(setup_http, google):
    """The load form appears once the Parish profile and ParishSoft key are saved."""
    publish("parishsoft")
    with web_login():
        browser = started()
        blocked = browser.get("/admin/setup/source")
        assert blocked.status_code == 200
        assert b"Save the Parish profile and ParishSoft connection" in blocked.content
        assert b'value="load"' not in blocked.content
        save(browser, "parish", VALUES["parish"])
        staged = post(
            browser,
            "/admin/setup/credentials/parishsoft",
            {
                "candidate": CANDIDATE.decode(),
                "organization_id": "1",
                "version": str(SetupAttempt.objects.get().version),
            },
        )
        assert staged["Location"] == "/admin/setup/mail"
        ready = browser.get("/admin/setup/source")
        assert b'value="load"' in ready.content


def test_credential_prerequisites_and_format_are_inline_form_errors(setup_http, google):
    """Nothing is staged, and the page re-renders instead of a JSON body."""
    with web_login():
        browser = started()
        page = browser.get("/admin/setup/credentials/google_workspace")
        assert page.status_code == 200
        assert b"Save the outgoing email settings and Testing recipient" in (
            page.content
        )
        response = post(
            browser,
            "/admin/setup/credentials/google_workspace",
            {"candidate": "{}", "version": str(SetupAttempt.objects.get().version)},
        )
        assert response.status_code == 400
        assert response["Content-Type"].startswith("text/html")
        assert b"Save the outgoing email settings" in response.content
        malformed = post(
            browser,
            "/admin/setup/credentials/parishsoft",
            {
                "candidate": "not a key",
                "organization_id": "1",
                "version": str(SetupAttempt.objects.get().version),
            },
        )
        assert malformed.status_code == 400
        assert b"does not look like a ParishSoft API key" in malformed.content
        assert b"not a key" not in malformed.content
        assert not SetupSealedCredential.objects.exists()
