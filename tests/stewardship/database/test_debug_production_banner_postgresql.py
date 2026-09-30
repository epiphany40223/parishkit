"""Every Admin page warns when debug logging is on in Production (#383)."""

import pytest
from django.db import connection, transaction

from parishkit.stewardship.observability import DEBUG_LOGGING_VARIABLE

from ..policy_factory import address
from .auth_builders import signed_in
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_security_events_postgresql import home, signed_in_as
from .test_setup_views_postgresql import setup_http  # noqa: F401
from .test_user_rule_views_postgresql import web
from .test_user_views_postgresql import add_rules

pytestmark = pytest.mark.django_db(transaction=True)
BANNER = "Debug logging is on in Production"
# A browser navigation, so a refusal is rendered as the Admin HTML error page.
PAGE = {"HTTP_ACCEPT": "text/html,*/*;q=0.8", "HTTP_SEC_FETCH_MODE": "navigate"}


def force_mode(mode):
    """Set the deployment mode directly, as a disposable fixture edit.

    Only the activation workflow may change the mode, and it needs a whole
    go-live; this suspends the row's guards for this one statement so the
    banner can be checked on its own. Every request afterwards runs guarded.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_system_configuration DISABLE TRIGGER USER"
        )
        cursor.execute("UPDATE stewardship_system_configuration SET mode=%s", [mode])
        cursor.execute(
            "ALTER TABLE stewardship_system_configuration ENABLE TRIGGER USER"
        )


@pytest.mark.parametrize(
    ("mode", "debug", "shown"),
    [
        ("production", "1", True),
        ("production", "0", False),
        ("production", None, False),
        ("testing", "1", False),
    ],
    ids=["production-debug", "production-off", "production-unset", "testing-debug"],
)
def test_banner_only_for_debug_logging_in_production(
    auth_service, google, monkeypatch, mode, debug, shown
):
    """Administrators and Staff alike see how to turn debug logging off."""
    add_rules(auth_service.store, address("staff@example.org", ("staff",)))
    browser, login = signed_in()
    assert login.status_code == 302
    staff = signed_in_as(google, "staff@example.org", "staff-subject")
    force_mode(mode)
    if debug is None:
        monkeypatch.delenv(DEBUG_LOGGING_VARIABLE, raising=False)
    else:
        monkeypatch.setenv(DEBUG_LOGGING_VARIABLE, debug)
    for page in (home(browser), home(staff)):
        assert (BANNER in page) is shown
        assert ("PARISHKIT_DEBUG_LOGGING to 0" in page) is shown
    # An Admin error page carries the same chrome, banner included.
    with web():
        refused = browser.post(
            "/admin/critical-events/acknowledge",
            {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value},
            **PAGE,
        )
    assert refused.status_code == 400
    assert refused["Content-Type"].startswith("text/html")
    assert (BANNER in refused.content.decode()) is shown


def test_banner_on_the_setup_wizard(setup_http, google, monkeypatch):  # noqa: F811
    """The setup-only chrome warns too; the wizard is every page until setup ends."""
    monkeypatch.setenv(DEBUG_LOGGING_VARIABLE, "1")
    with web_login():
        browser, _ = signed_in()
        page = browser.get("/admin/setup")
    assert page.status_code == 200 and BANNER not in page.content.decode()
    # The fixture edit needs the schema owner, not the web login.
    force_mode("production")
    with web_login():
        page = browser.get("/admin/setup")
    # Setup refuses outside Testing, but its setup-only chrome still warns.
    text = page.content.decode()
    assert page.status_code == 503 and "Initial setup" in text
    assert BANNER in text
