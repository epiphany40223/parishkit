"""HTTP setup admission, private draft isolation, CSRF and exact concurrent edits."""

from dataclasses import replace

import pytest

from parishkit.stewardship.accounts.configuration_models import Parish
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.setup_forms import initial_values
from parishkit.stewardship.accounts.setup_models import SetupAttempt, SetupDraftSection

from ..test_setup_forms import VALUES
from . import auth_builders
from .auth_builders import signed_in
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_expiry_postgresql import sweep

pytestmark = pytest.mark.django_db(transaction=True)


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
        path, values | {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value}
    )


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


@pytest.mark.parametrize("step", VALUES)
def test_original_browser_saves_and_revisits_public_steps(setup_http, google, step):
    """Actual web grants support requests and templates without activation."""
    with web_login():
        browser = started()
        assert browser.get("/admin/setup/" + step).status_code == 200
        version = SetupAttempt.objects.get().version
        response = post(
            browser,
            "/admin/setup/" + step,
            initial_values(step, VALUES[step]) | {"version": str(version)},
        )
        assert response.status_code == 302, response.content
        assert response["Location"] == "/admin/setup"
        assert SetupDraftSection.objects.get().values == VALUES[step]
        assert b"Saved temporarily" in browser.get("/admin/setup").content
        activity = PortalSession.objects.get().last_activity_at
        assert browser.get("/admin/setup/" + step).status_code == 200
        assert PortalSession.objects.get().last_activity_at == activity
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
    assert invalid.status_code == 400 and b"id_phone_error" in invalid.content
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
    assert (
        browser.get("/admin/configuration/integrations")["Location"] == "/admin/setup"
    )
    assert not SetupDraftSection.objects.exists()
