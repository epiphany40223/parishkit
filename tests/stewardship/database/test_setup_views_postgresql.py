"""HTTP setup admission, private draft isolation, CSRF and exact concurrent edits."""

import html
from dataclasses import replace

import pytest

from parishkit.stewardship.accounts.configuration_models import Parish
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.setup_forms import initial_values
from parishkit.stewardship.accounts.setup_models import SetupAttempt, SetupDraftSection
from parishkit.stewardship.web.presentation import phone

from ..test_setup_forms import VALUES
from . import auth_builders
from .auth_builders import signed_in
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_expiry_postgresql import sweep

pytestmark = pytest.mark.django_db(transaction=True)
# Save and continue goes to the next page of the wizard's one ordered list.
# The wizard's order (#1353): ParishSoft and its load come after access.
NEXT = {
    "parish": "/admin/setup/mail",
    "access": "/admin/setup/credentials/parishsoft",
    "mail": "/admin/setup/testing",
    "slack": "/admin/setup/branding",
    "testing": "/admin/setup/credentials/google_workspace",
}


@pytest.fixture
def setup_http(request, bootstrapped, monkeypatch, settings):  # noqa: F811
    """Keep actual OAuth and Valkey, replacing only the fixture's configuration root."""
    store, root, _, actor = bootstrapped
    monkeypatch.setattr(auth_builders, "initialized", lambda _: (store, root, actor))
    service = request.getfixturevalue("auth_service")
    settings.STEWARDSHIP_AUTH_RUNTIME = replace(service, setup_complete=lambda: False)
    return settings.STEWARDSHIP_AUTH_RUNTIME


def post(browser, path, values):
    """Use the current browser CSRF cookie on every ordinary form submission."""
    return browser.post(
        path, values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


def admin_session(browser):
    """The server-side session behind this browser's admin cookie.

    The admin cookie is not Django's default session cookie name, so the test
    client's own ``session`` property cannot see it.
    """
    from parishkit.stewardship.accounts.admin_session_store import SessionStore

    return SessionStore(session_key=browser.cookies["pk_admin"].value)


def started():
    """Only an explicit POST starts temporary work; callback and GET do not."""
    browser, response = signed_in()
    assert response.status_code == 302
    assert browser.get("/admin/setup").status_code == 200
    assert post(browser, "/admin/setup", {"action": "start"}).status_code == 302
    return browser


def test_setup_get_is_passive_and_shows_testing_without_fake_completion(
    setup_http, google
):
    """The empty deployment has an actionable form, not an active Parish projection."""
    with web_login():
        browser, _ = signed_in()
        activity = PortalSession.objects.get().last_activity_at
        response = browser.get("/admin/setup")
        assert response.status_code == 200
        assert b"Start setup" in response.content
        assert b"Testing mode" in response.content
        assert response["Cache-Control"] == "no-store"
        assert not SetupAttempt.objects.exists()
        assert PortalSession.objects.get().last_activity_at == activity
        assert browser.get("/admin/setup/parish")["Location"] == "/admin/setup"
    assert not Parish.objects.exists() and not setup_http.configured()


def test_original_browser_saves_and_revisits_public_steps(setup_http, google):
    """One real wizard session saves each public step without activating it."""
    with web_login():
        browser = started()
        for step, values in VALUES.items():
            assert browser.get("/admin/setup/" + step).status_code == 200, step
            version = SetupAttempt.objects.get().version
            response = post(
                browser,
                "/admin/setup/" + step,
                initial_values(step, values) | {"version": str(version)},
            )
            assert response.status_code == 302, (step, response.content)
            assert response["Location"] == NEXT[step]
            assert SetupDraftSection.objects.get(step=step).values == values
            assert b"Completed" in browser.get("/admin/setup").content
            activity = PortalSession.objects.get().last_activity_at
            revisit = browser.get("/admin/setup/" + step)
            assert revisit.status_code == 200
            assert PortalSession.objects.get().last_activity_at == activity
            # Going back shows what was saved, not an empty form.
            for value in values.values():
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, str) and item:
                        # Telephone numbers are stored as E.164 and shown in
                        # the "+1 (212) 555-1234" display format.
                        shown = phone(item) if item.startswith("+1") else item
                        assert html.escape(shown).encode() in revisit.content, item
        assert dict(SetupDraftSection.objects.values_list("step", "values")) == VALUES
    assert not Parish.objects.exists()


def test_csrf_unknown_fields_duplicates_and_stale_versions_do_not_write(
    setup_http, google
):
    """A hidden credential cannot be echoed or stored by the public form owner."""
    browser = started()
    values = VALUES["parish"] | {"version": "1"}
    assert browser.post("/admin/setup/parish", values).status_code == 403
    for invalid in (
        {"candidate": "synthetic-private"},
        {"name": ["one", "two"]},
        {"version": "0001"},
    ):
        response = post(browser, "/admin/setup/parish", values | invalid)
        assert response.status_code == 400
        assert b"synthetic-private" not in response.content
    invalid = post(browser, "/admin/setup/parish", values | {"phone": "bad"})
    # The field keeps a reserved error line (#1577); the refusal fills it.
    assert invalid.status_code == 400
    assert (
        b'id="id_phone_error"><li>Enter a ten-digit US telephone number'
        in invalid.content
    )
    assert not SetupDraftSection.objects.exists()
    assert post(browser, "/admin/setup/parish", values).status_code == 302
    assert post(browser, "/admin/setup/parish", values).status_code == 409
    assert SetupDraftSection.objects.get().version == 1


def test_cancel_clears_values_and_cannot_restart_under_the_same_login(
    setup_http, google
):
    """The expired receipt remains visible but the saved settings do not."""
    browser = started()
    post(browser, "/admin/setup/parish", VALUES["parish"] | {"version": "1"})
    attempt = SetupAttempt.objects.get()
    with web_login():
        response = post(
            browser, "/admin/setup", {"action": "cancel", "attempt": str(attempt.pk)}
        )
        assert response.status_code == 302
        assert SetupDraftSection.objects.get().values == {}
        page = browser.get("/admin/setup")
        assert b"attempt has ended" in page.content
        assert b"Sample Parish" not in page.content
        assert (
            post(browser, "/admin/setup", {"action": "start"})["Location"]
            == "/admin/setup"
        )
        assert SetupAttempt.objects.count() == 1
        assert browser.get("/admin/setup/parish")["Location"] == "/admin/setup"


def test_another_login_cannot_view_or_restart_a_live_attempt(setup_http, google):
    """The same Google account's different browser still has no draft authority."""
    first = started()
    post(first, "/admin/setup/parish", VALUES["parish"] | {"version": "1"})
    second, _ = signed_in()
    assert b"Sample Parish" not in second.get("/admin/setup").content
    assert post(second, "/admin/setup", {"action": "start"}).status_code == 409
    assert second.get("/admin/setup/parish")["Location"] == "/admin/setup"
    assert post(first, "/admin/logout", {}).status_code == 302
    assert sweep() == 1
    assert post(second, "/admin/setup", {"action": "start"}).status_code == 302
    assert SetupAttempt.objects.count() == 2


def test_unrecognized_setup_step_and_query_have_no_side_effects(setup_http, google):
    """The namespace allowlist cannot expose arbitrary configuration editors."""
    browser = started()
    assert browser.get("/admin/setup/passwords").status_code == 404
    assert browser.get("/admin/setup/parish?credential=private").status_code == 400
    assert browser.get("/admin/system/integrations/")["Location"] == "/admin/setup"
    assert not SetupDraftSection.objects.exists()


def test_sign_out_and_cancel_expires_the_attempt_now_with_reason_cancelled(
    setup_http, google
):
    """The cancelling Sign out (#2272) fences the attempt before the scheduler."""
    browser = started()
    post(browser, "/admin/setup/parish", VALUES["parish"] | {"version": "1"})
    attempt = SetupAttempt.objects.get()
    with web_login():
        page = browser.get("/admin/setup").content
        # The menu's Sign out names the dialog and the owned attempt.
        assert b'data-signout-confirm="setup-signout-dialog"' in page
        assert f'data-signout-attempt="{attempt.pk}"'.encode() in page
        assert b"Sign out and cancel setup" in page
        response = post(browser, "/admin/logout", {"cancel_setup": str(attempt.pk)})
        assert response.status_code == 302
        assert response["Location"] == "/admin/login?setup=cancelled"
    row = SetupAttempt.objects.get()
    assert row.state == "expired" and row.expiry_reason == "cancelled"
    assert row.actor_id == row.owner_id
    assert SetupDraftSection.objects.get().values == {}
    # The session is gone too, and the scheduler has nothing left to expire.
    assert sweep() == 0
    login_page = browser.get(response["Location"]).content
    assert b"Setup was cancelled." in login_page
    assert b"original keys" not in login_page


def test_plain_sign_out_still_expires_with_reason_session(setup_http, google):
    """Without the flag, logout is today's revocation and the scheduler expires it."""
    browser = started()
    attempt = SetupAttempt.objects.get()
    assert post(browser, "/admin/logout", {}).status_code == 302
    assert SetupAttempt.objects.get(pk=attempt.pk).state == "collecting"
    assert sweep() == 1
    row = SetupAttempt.objects.get()
    assert row.state == "expired" and row.expiry_reason == "session"
    assert b"Setup was cancelled." not in browser.get("/admin/login").content


def test_sign_out_cannot_cancel_another_logins_attempt(setup_http, google):
    """A posted id of someone else's attempt signs out but cancels nothing."""
    first = started()
    attempt = SetupAttempt.objects.get()
    second, _ = signed_in()
    with web_login():
        response = post(second, "/admin/logout", {"cancel_setup": str(attempt.pk)})
    # Signed out, with no "cancelled" notice, and the first login's attempt lives.
    assert response.status_code == 302 and response["Location"] == "/admin/login"
    assert SetupAttempt.objects.get(pk=attempt.pk).state == "collecting"
    assert first.get("/admin/setup").status_code == 200
    # The refused cancellation still revoked the second login's own session.
    assert PortalSession.objects.filter(revoked_at__isnull=True).count() == 1


def test_sign_out_with_a_malformed_or_stale_attempt_id_just_signs_out(
    setup_http, google
):
    """The flag never traps Sign out: bad or repeated ids fall back to a plain one."""
    browser = started()
    attempt = SetupAttempt.objects.get()
    response = post(browser, "/admin/logout", {"cancel_setup": "not-a-uuid"})
    assert response["Location"] == "/admin/login"
    assert SetupAttempt.objects.get(pk=attempt.pk).state == "collecting"


def test_sign_out_and_cancel_handles_loading_and_already_ended_attempts(
    setup_http, google
):
    """A loading attempt cancels like a collecting one; an ended one just signs out."""
    from django.db.models import F

    from parishkit.stewardship.campaigns.work_locks import work_transaction

    from .test_taskrun_postgresql import new

    browser = started()
    row = SetupAttempt.objects.get()
    task = new(
        task_type="setup_source_load",
        actor_id=row.owner_id,
        domain_request_id=row.pk,
    )
    with work_transaction():
        SetupAttempt.objects.filter(pk=row.pk).update(
            source_task_id=task.run_id, state="loading", version=F("version") + 1
        )
    with web_login():
        response = post(browser, "/admin/logout", {"cancel_setup": str(row.pk)})
    assert response["Location"] == "/admin/login?setup=cancelled"
    row = SetupAttempt.objects.get()
    assert row.state == "expired" and row.expiry_reason == "cancelled"
    # Repeating it (the attempt has ended) is a plain Sign out, no notice.
    again = started()
    ended = SetupAttempt.objects.exclude(pk=row.pk).get()
    with web_login():
        post(again, "/admin/setup", {"action": "cancel", "attempt": str(ended.pk)})
        response = post(again, "/admin/logout", {"cancel_setup": str(ended.pk)})
    assert response["Location"] == "/admin/login"
    assert SetupAttempt.objects.get(pk=ended.pk).expiry_reason == "cancelled"
