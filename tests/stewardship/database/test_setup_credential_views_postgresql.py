"""Write-only setup credential HTTP workflow over deployed web grants."""

# ruff: noqa: F811 -- pytest resolves the imported fixture dependencies.

import pytest

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
from parishkit.stewardship.accounts.setup_lookups import current_lookup
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_secret_models import SetupSealedCredential

from ..test_integration_candidates import account
from ..test_setup_forms import VALUES
from .auth_builders import signed_in
from .setup_lookup_builders import answer_lookup
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_credentials_postgresql import CANDIDATE, publish
from .test_setup_views_postgresql import post, setup_http, started  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/setup/credentials/parishsoft"


def lookup_values(**changes):
    """The page's lookup buttons' fields, for the open lookup (#1554)."""
    lookup = current_lookup(SetupAttempt.objects.get().pk)
    values = {
        "action": "choose",
        "lookup": str(lookup.identifier),
        "version": str(SetupAttempt.objects.get().version),
    }
    if len(lookup.organizations) == 1:
        values["organization"] = str(lookup.organizations[0].identifier)
    return values | changes


def choose_on_page(browser, **changes):
    """Post the page's Yes (or Use this organization) for the open lookup."""
    return post(browser, URL, lookup_values(**changes))


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
                    "version": str(SetupAttempt.objects.get().version),
                },
            ).status_code
            == 302
        )
    # The installer answers the key's organization lookup (#1554), and the
    # Administrator confirms the one organization.
    answer_lookup()
    with web_login():
        assert choose_on_page(browser).status_code == 302
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
            "version": str(SetupAttempt.objects.get().version),
        }
        assert browser.post(URL, values).status_code == 403
        # The page has no organization field (#1554): one is unexpected input.
        invalid = post(browser, URL, values | {"organization_id": "1"})
        assert invalid.status_code == 400
        assert CANDIDATE not in invalid.content
        assert not SetupSealedCredential.objects.exists()
    # The refusal's log entry reopens the test's connection; the web login
    # is set on it again, as every deployed web connection is.
    with web_login():
        accepted = post(browser, URL, values)
        # Back to this page, which asks ParishSoft for the organizations.
        assert accepted.status_code == 302 and accepted["Location"] == URL
        assert SetupSealedCredential.objects.defer("ciphertext").get().settings == {}
        page = browser.get(URL)
        assert page.status_code == 200
        assert CANDIDATE not in page.content
        assert b"Asking ParishSoft which organizations this key reaches" in (
            page.content
        )
        assert b'data-reread-every="5000"' in page.content
        assert b'name="candidate"' not in page.content
    answer_lookup()
    with web_login():
        page = browser.get(URL)
        assert b"This key reaches one ParishSoft organization: " in page.content
        assert b"<strong>St. Example Parish (1)</strong>" in page.content
        assert b"Yes, use this organization" in page.content
        assert b"No, enter a different key" in page.content
        assert b"data-reread-every" not in page.content
        chosen = choose_on_page(browser)
        # Load parish data is blocked until the Parish profile is saved too,
        # so the choice opens that step needing work (#1476).
        assert chosen.status_code == 302 and chosen["Location"] == "/admin/setup/parish"
        page = browser.get(URL)
        # The secret is never shown, but its saved state and scope are.
        assert b"A ParishSoft API key is saved for St. Example Parish (1)." in (
            page.content
        )
        assert b'name="organization_id"' not in page.content
        assert b'type="password" name="candidate"' in page.content
        assert b"Save and continue" in page.content
        assert b"Seal" not in page.content
        assert not SecretReplacementRequest.objects.exists()
        # An empty key keeps the saved one and its organization.
        staged = SetupSealedCredential.objects.defer("ciphertext").get().version
        version = str(SetupAttempt.objects.get().version)
        kept = post(browser, URL, {"candidate": "", "version": version})
        assert kept.status_code == 302 and kept["Location"] == "/admin/setup/parish"
        assert SetupSealedCredential.objects.defer("ciphertext").get().version == staged
        assert SetupSealedCredential.objects.defer("ciphertext").get().settings == {
            "organization_id": 1
        }
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


def test_a_key_follows_its_rule_at_the_field(setup_http, google):
    """A key with a space inside is refused at its field in the key rule's
    words, nothing staged (#1577); spaces around a pasted key are stripped,
    so the sealed key is the key itself."""
    from parishkit.stewardship.accounts.integration_forms import KEY_RULES

    private = publish("parishsoft")
    with web_login():
        browser = started()
        version = str(SetupAttempt.objects.get().version)
        values = {"version": version}
        refused = post(browser, URL, values | {"candidate": "two words"})
        assert refused.status_code == 400
        message = str(KEY_RULES["parishsoft"].message).encode()
        assert message in refused.content.replace(b"&#x27;", b"'")
        assert b'aria-invalid="true"' in refused.content
        assert not SetupSealedCredential.objects.exists()
        padded = post(browser, URL, values | {"candidate": f" {CANDIDATE.decode()} "})
        assert padded.status_code == 302, padded.content
    row = SetupSealedCredential.objects.get()
    assert private.open(row.pk, row.ciphertext) == CANDIDATE


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


def test_the_slack_credential_page_is_gone(setup_http, google):
    """The Slack bot token is entered on the Slack notifications step
    (#1578): the old Slack connection address answers 404 to a read and to a
    post, with no redirect, and stages nothing."""
    publish("slack")
    slack = "/admin/setup/credentials/slack"
    with web_login():
        browser = started()
        version = str(SetupAttempt.objects.get().version)
        on = {"enabled": "on", "channel_id": "CEXAMPLE", "version": version}
        assert post(browser, "/admin/setup/slack", on).status_code == 400
        assert browser.get(slack).status_code == 404
        token = {
            "candidate": "xoxb-synthetic-slack-token",
            "version": str(SetupAttempt.objects.get().version),
        }
        assert post(browser, slack, token).status_code == 404
    assert not SetupSealedCredential.objects.filter(target="slack").exists()


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
            {"organization_id": "1"},
            {"version": "0001"},
        ):
            response = post(
                browser,
                URL,
                {"candidate": CANDIDATE.decode(), "version": "1"} | extra,
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
            {"candidate": CANDIDATE.decode(), "version": "1"},
        )
        assert response.status_code == 503
        assert CANDIDATE not in response.content
        assert not SetupSealedCredential.objects.exists()


@pytest.mark.parametrize("minutes", [30, 61, 180])
def test_an_old_sign_in_opens_and_saves_a_credential_page(setup_http, google, minutes):
    """A sign-in of any age opens and saves a setup credential page (#1547)."""
    import time

    publish("parishsoft")
    google[0]["auth_time"] = int(time.time()) - minutes * 60
    with web_login():
        browser = started()
        page = browser.get(URL)
        assert page.status_code == 200
        assert b"data-step-up-notice" not in page.content
        response = post(
            browser,
            URL,
            {
                "candidate": CANDIDATE.decode(),
                "version": str(SetupAttempt.objects.get().version),
            },
        )
        assert response.status_code == 302, response.content
    assert PortalSession.objects.count() == 1
    assert SetupSealedCredential.objects.count() == 1
