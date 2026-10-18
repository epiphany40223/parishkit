# ruff: noqa: F811 -- pytest resolves the imported fixture dependencies.
"""Each setup test is bound to its own inputs (#1578), as SQL enforces it.

Migration 0169 (``schema/migrations/0169_setup_test_binding.sql``) replaces
the email and Slack live checks without their joins on a succeeded parish
data load and on the exact draft version, and the readiness guard with the
tests bound to the staged credential and the settings sealed with it. These
tests check the file's own DO block against the bodies it replaces.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_setup_views_postgresql import setup_http  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0169_setup_test_binding.sql"


def earlier(file, name):
    """``name``'s CREATE OR REPLACE text in an earlier frozen file."""
    text = (MIGRATIONS / file).read_text(encoding="utf-8")
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    tag = re.search(r" AS (\$[A-Za-z_]*\$)", text[start:])[1]
    body = text.index(tag, start + text[start:].index(" AS ") + 4) + len(tag)
    return text[start : text.index(tag + ";", body) + len(tag) + 1]


# The bodies 0169 replaces: 0102's live checks and 0055's readiness guard.
OLD = {
    "stewardship_setup_mail_live_v1": "0102_setup_load_void.sql",
    "stewardship_setup_slack_live_v1": "0102_setup_load_void.sql",
    "stewardship_setup_readiness_guard_v1": "0055_setup_users.sql",
}


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


@pytest.mark.parametrize("name", sorted(OLD))
def test_the_check_refuses_each_old_body_and_the_file_installs_the_new(name):
    """The DO block passes on the installed functions and fails once any one
    is the body 0169 replaces; the whole file, run over that body, installs
    the new one again."""
    text = FROZEN.read_text(encoding="utf-8")
    # The check compares the grants the file records before replacing.
    grants = re.search(r"^SELECT set_config\(.*?, true\);", text, re.M | re.S)[0]
    check = re.search(r"^DO \$check\$.*?\$check\$;", text, re.M | re.S)[0]
    old = earlier(OLD[name], name)
    assert "original.state='succeeded'" in old or "candidate_digest" in old
    with connection.cursor() as cursor:
        with transaction.atomic():
            cursor.execute(grants)
            cursor.execute(check)
        with (
            pytest.raises(DatabaseError, match="Migration 0169"),
            transaction.atomic(),
        ):
            cursor.execute(grants)
            cursor.execute(old)
            cursor.execute(check)
        with pytest.raises(Rollback), transaction.atomic():
            cursor.execute(old)
            cursor.execute(text)
            cursor.execute(
                "SELECT strpos(prosrc, 'attempt.version=$2')=0"
                " AND strpos(prosrc, 'original.state=''succeeded''')=0"
                " FROM pg_proc WHERE proname=%s",
                [name],
            )
            assert cursor.fetchone() == (True,)
            raise Rollback


def mail_ready(browser):
    """Save the steps Test email needs, over HTTP, and stage the Google key.

    Parish profile, Outgoing email settings and Testing recipient, then
    Google Workspace connection: nothing later, and no parish data load.
    """
    from parishkit.stewardship.accounts.setup_models import SetupAttempt

    from ..test_integration_candidates import account
    from ..test_setup_forms import VALUES
    from .test_setup_views_postgresql import post

    def version():
        """The attempt's current optimistic-lock version as a form value."""
        return str(SetupAttempt.objects.get().version)

    for step in ("parish", "mail", "testing"):
        values = VALUES[step] | {"version": version()}
        assert post(browser, f"/admin/setup/{step}", values).status_code == 302
    key = {"candidate": account().decode(), "version": version()}
    saved = post(browser, "/admin/setup/credentials/google_workspace", key)
    assert saved.status_code == 302
    # Saving the key opens the test that proves it (#1578).
    assert saved["Location"] == "/admin/setup/mail-test"
    return version


def send(browser):
    """Send the test email from its page, as the page's own form does."""
    from .test_setup_views_postgresql import post

    page = browser.get("/admin/setup/mail-test")
    assert page.status_code == 200
    form = page.context["form"]
    values = {
        "test_token": form["test_token"].value(),
        "request_key": str(form["request_key"].value()),
    }
    return post(browser, "/admin/setup/mail-test", values)


def test_the_email_test_needs_only_its_own_steps(setup_http, google):
    """Test email opens and queues a test with only the parish, mail,
    Testing and Google Workspace steps done: no logos, access, ParishSoft key
    or load (#1578); the SQL live check admits it."""
    from parishkit.stewardship.accounts.setup_delivery_models import (
        SetupMailDelivery,
    )
    from parishkit.stewardship.accounts.setup_models import SetupAttempt

    from .test_runtime_auth_grants_postgresql import web_login
    from .test_setup_credentials_postgresql import publish
    from .test_setup_views_postgresql import started

    publish("google_workspace", material=b"g" * 32)
    with web_login():
        browser = started()
        mail_ready(browser)
        assert SetupAttempt.objects.get().source_task_id is None
        assert send(browser).status_code == 302
    assert SetupMailDelivery.objects.get().state == "queued"


def test_an_accepted_test_survives_later_steps_until_its_own_inputs_change(
    setup_http, google
):
    """An accepted email test stays done while a later step is saved, and
    goes stale, with the step that fixes it named, once its own input (the
    Testing recipient) changes (#1578)."""
    from parishkit.stewardship.accounts.setup_delivery_models import (
        SetupMailDelivery,
    )
    from parishkit.stewardship.accounts.setup_mail import (
        _status,
        begin_submission,
        finish_submission,
    )
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.readiness_delivery import DeliveryOutcome

    from ..test_setup_forms import VALUES
    from .test_background_grants_postgresql import task_login
    from .test_runtime_auth_grants_postgresql import web_login
    from .test_setup_credentials_postgresql import publish
    from .test_setup_mail_postgresql import claim
    from .test_setup_views_postgresql import post, started

    publish("google_workspace", material=b"g" * 32)
    with web_login():
        browser = started()
        version = mail_ready(browser)
        assert send(browser).status_code == 302
    status = _status(SetupMailDelivery.objects.get())
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        owner = claim(status)
        begin_submission(status.identifier, owner)
        finish_submission(status.identifier, owner, DeliveryOutcome.ACCEPTED)
    with web_login():
        access = {name: "\n".join(value) for name, value in VALUES["access"].items()}
        later = post(browser, "/admin/setup/access", access | {"version": version()})
        assert later.status_code == 302
        page = browser.get("/admin/setup/mail-test")
        assert page.status_code == 200 and page.context["tested"] is True
        assert page.context["wizard"].step("mail_test").state == "done"
        changed = {"testing_recipient": "another@example.org", "version": version()}
        saved = post(browser, "/admin/setup/testing", changed)
        # The key is stale now: the save opens the step that stages it again.
        assert saved["Location"] == "/admin/setup/credentials/google_workspace"
        page = browser.get("/admin/setup/mail-test")
        assert page.status_code == 503
        blocked = page.context["blocked"]
        assert blocked.state == "blocked"
        assert blocked.fix_url == "/admin/setup/credentials/google_workspace"
