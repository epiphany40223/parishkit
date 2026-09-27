"""Write-only setup credential HTTP workflow over deployed web grants."""

# ruff: noqa: F811 -- pytest resolves the imported fixture dependencies.

import pytest

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_secret_models import SetupSealedCredential

from ..test_integration_candidates import account
from ..test_setup_forms import VALUES
from .auth_builders import signed_in
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_credentials_postgresql import CANDIDATE, publish
from .test_setup_views_postgresql import post, setup_http, started  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/setup/credentials/parishsoft"


def test_original_browser_starts_one_source_load_with_csrf(setup_http, google):
    """The real start form redirects to its original correlated progress route."""
    from parishkit.stewardship.jobs.models import TaskRun

    publish("parishsoft")
    with web_login():
        browser = started()
        assert (
            post(
                browser,
                "/admin/setup/parish",
                VALUES["parish"]
                | {
                    "version": str(SetupAttempt.objects.get().version),
                },
            ).status_code
            == 302
        )
        assert (
            post(
                browser,
                URL,
                {
                    "candidate": CANDIDATE.decode(),
                    "organization_id": "1",
                    "version": str(SetupAttempt.objects.get().version),
                },
            ).status_code
            == 302
        )
        attempt = SetupAttempt.objects.get()
        values = {
            "action": "load",
            "attempt": str(attempt.pk),
            "version": str(attempt.version),
        }
        assert browser.post("/admin/setup", values).status_code == 403
        response = post(browser, "/admin/setup", values)
        assert response.status_code == 302, response.content
        task = TaskRun.objects.get()
        assert str(task.pk) in response["Location"]
        assert browser.get(response["Location"]).status_code == 200
        repeated = post(browser, "/admin/setup", values)
        assert repeated["Location"] == response["Location"]
        assert TaskRun.objects.count() == 1
        assert SetupAttempt.objects.get().state == "loading"
    assert not setup_http.configured()


def test_write_only_form_real_csrf_staging_and_cancellation(setup_http, google):
    """Original browser can stage and cancel without a live replacement or echo."""
    private = publish("parishsoft")
    with web_login():
        browser = started()
        activity = PortalSession.objects.get().last_activity_at
        response = browser.get(URL)
        assert response.status_code == 200, response.content
        assert response["Cache-Control"] == "no-store"
        assert PortalSession.objects.get().last_activity_at == activity
        values = {
            "candidate": CANDIDATE.decode(),
            "organization_id": "1",
            "version": str(SetupAttempt.objects.get().version),
        }
        assert browser.post(URL, values).status_code == 403
        invalid = post(browser, URL, values | {"organization_id": "invalid"})
        assert invalid.status_code == 400
        assert CANDIDATE not in invalid.content
        assert not SetupSealedCredential.objects.exists()
        accepted = post(browser, URL, values)
        assert accepted.status_code == 302, accepted.content
        page = browser.get(URL)
        assert page.status_code == 200
        assert CANDIDATE not in page.content
        # The secret is never shown, but its saved state and scope are.
        assert b"A ParishSoft API key is saved (organization 1)." in page.content
        assert b'name="organization_id" value="1"' in page.content
        assert b'type="password" name="candidate"' in page.content
        assert b"Save and continue" in page.content
        assert b"Seal" not in page.content
        assert not SecretReplacementRequest.objects.exists()
        # An empty key keeps the saved one; nothing is re-sealed.
        staged = SetupSealedCredential.objects.get().version
        version = str(SetupAttempt.objects.get().version)
        kept = post(
            browser, URL, {"candidate": "", "organization_id": "1", "version": version}
        )
        assert kept.status_code == 302 and kept["Location"] == "/admin/setup/mail"
        assert SetupSealedCredential.objects.get().version == staged
        moved = post(
            browser, URL, {"candidate": "", "organization_id": "2", "version": version}
        )
        assert moved.status_code == 400
        assert b"enter the ParishSoft API key again" in moved.content
        assert SetupSealedCredential.objects.get().settings == {"organization_id": 1}
    row = SetupSealedCredential.objects.get()
    assert private.open(row.pk, row.ciphertext) == CANDIDATE
    with web_login():
        response = post(
            browser,
            "/admin/setup",
            {"action": "cancel", "attempt": str(row.attempt_id)},
        )
        assert response.status_code == 302, response.content
    row.refresh_from_db()
    assert row.ciphertext is None and row.scrubbed_at is not None


def test_a_stale_saved_key_must_be_entered_again(setup_http, google):
    """Keeping is refused once the settings the key was saved for have changed."""
    publish("google_workspace")
    workspace = "/admin/setup/credentials/google_workspace"

    def version():
        """The attempt's current optimistic-lock version as a form value."""
        return str(SetupAttempt.objects.get().version)

    with web_login():
        browser = started()
        for step in ("mail", "testing"):
            post(browser, f"/admin/setup/{step}", VALUES[step] | {"version": version()})
        empty = post(browser, workspace, {"candidate": "", "version": version()})
        assert empty.status_code == 400  # nothing saved yet: the key is required
        assert b"This field is required" in empty.content
        page = browser.get(workspace)
        assert b"<textarea" in page.content  # the JSON key stays multi-line
        staged = post(
            browser, workspace, {"candidate": account().decode(), "version": version()}
        )
        assert staged.status_code == 302, staged.content
        assert b"A Google Workspace mail credential is saved." in (
            browser.get(workspace).content
        )
        changed = {"testing_recipient": "other@example.org", "version": version()}
        post(browser, "/admin/setup/testing", changed)
        stale = browser.get(workspace)
        assert b"entered for settings you have changed since" in stale.content
        kept = post(browser, workspace, {"candidate": "", "version": version()})
        assert kept.status_code == 400
        assert b"Enter it again so it matches the current settings" in kept.content


def test_credential_post_rejects_undeclared_duplicate_and_stale_fields(
    setup_http,
    google,
):
    """Bad request shapes do not echo a submitted secret or persist a candidate."""
    with web_login():
        browser = started()
        for extra in (
            {"target": "slack"},
            {"settings": "private-extra"},
            {"organization_id": ["1", "2"]},
            {"version": "0001"},
        ):
            response = post(
                browser,
                URL,
                {
                    "candidate": CANDIDATE.decode(),
                    "organization_id": "1",
                    "version": "1",
                }
                | extra,
            )
            assert response.status_code == 400, extra
            assert CANDIDATE not in response.content
            assert not SetupSealedCredential.objects.exists()


def test_other_login_and_missing_handoff_fail_without_persisting(setup_http, google):
    """A known URL or form version cannot adopt another browser's live attempt."""
    with web_login():
        first = started()
        second, _ = signed_in()
        assert second.get(URL)["Location"] == "/admin/setup"
        response = post(
            first,
            URL,
            {"candidate": CANDIDATE.decode(), "organization_id": "1", "version": "1"},
        )
        assert response.status_code == 503
        assert CANDIDATE not in response.content
        assert not SetupSealedCredential.objects.exists()


def test_lapsed_freshness_offers_step_up_that_keeps_the_same_draft(setup_http, google):
    """A page shows Confirm with Google; confirming returns to the same setup."""
    import time

    publish("parishsoft")
    google[0]["auth_time"] = int(time.time()) - 600
    with web_login():
        browser = started()
        attempt = SetupAttempt.objects.get()
        page = browser.get(
            URL, HTTP_ACCEPT="text/html,*/*;q=0.8", HTTP_SEC_FETCH_MODE="navigate"
        )
        assert page.status_code == 403
        assert page["Content-Type"].startswith("text/html")
        assert page["Cache-Control"] == "no-store"
        body = page.content.decode()
        assert "Confirm with Google" in body
        assert 'action="/admin/login"' in body
        assert f'name="next" value="{URL}"' in body
        script = browser.get(URL, HTTP_ACCEPT="application/json")
        assert script.status_code == 403
        assert script.json()["errors"][0]["code"] == "denied"
        del google[0]["auth_time"]
        _, response = signed_in(browser, next=URL)
        assert response["Location"] == URL
        assert browser.get(URL).status_code == 200
        response = post(
            browser,
            URL,
            {
                "candidate": CANDIDATE.decode(),
                "organization_id": "1",
                "version": str(SetupAttempt.objects.get().version),
            },
        )
        assert response.status_code == 302, response.content
    assert SetupAttempt.objects.get().pk == attempt.pk
    assert PortalSession.objects.count() == 1
    assert SetupSealedCredential.objects.get().attempt_id == attempt.pk
