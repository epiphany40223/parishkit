"""Take a backup now on the System health page (ADM-13 PR 3b).

Preview, then confirm, in the shared review dialog (#1356): the preview is a
one-sentence confirmation, Apply's answer draws the region again, and a
refusal is a fragment the dialog shows as drawn; a stale sign-in gets the
"Confirm with Google" step-up; a repeated confirmation returns the
first request; one request at a time; Administrators only; the audit event
names the request.
"""

import re
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.backup_models import BackupRequest

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)
PAGE = "/admin/system/health/"
URL = PAGE


def post(browser, **values):
    """A form submission with the Admin CSRF token, under the web login."""
    token = browser.cookies["pk_admin_csrf"].value
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.post(
            URL, {"csrfmiddlewaretoken": token, **values}, HTTP_ACCEPT="text/html"
        )


def key_of(response):
    """The request key the preview carries to its confirmation."""
    return re.search(
        r'name="key" value="([0-9a-f-]{36})"', response.content.decode()
    ).group(1)


def test_preview_then_confirm_records_one_request_and_its_audit(auth_service, google):
    """Each step answers the dialog; a repeat returns the first request."""
    browser, _ = signed_in()
    preview = post(browser, action="preview")
    assert preview.status_code == 200
    body = preview.content.decode()
    # A confirmation, not the page: one sentence, the dialog's description,
    # and none of the last backup's facts the Backups details show.
    assert "<h1" not in body and 'id="backup-now"' not in body
    assert '<div data-review-answer data-review-done="Backup requested.">' in body
    assert (
        '<p id="backup-review-summary">This asks the server to take one backup'
        " soon, of the same kind as the scheduled ones; off-site copies are not"
        " set up, so it stays on the server only.</p>"
    ) in body
    assert "No backup has been recorded yet." not in body
    assert "retention" not in body and "Confirm: take a backup now" not in body
    assert re.search(r'name="key" value="[0-9a-f-]{36}" data-review-preview', body)
    assert BackupRequest.objects.count() == 0
    key = key_of(preview)
    confirmed = post(browser, action="confirm", key=key)
    assert confirmed.status_code == 200
    # Apply's answer is the region drawn again, its button greyed; the
    # dialog redraws the page from it, so the page is not read again.
    done = confirmed.content.decode()
    assert "<h1" not in done and 'id="backup-now"' in done
    assert 'disabled aria-describedby="backup-now-reason"' in done
    assert "data-live-status" not in done
    request = BackupRequest.objects.get()
    assert str(request.pk) == key and request.state == "waiting"
    event = AuditEvent.objects.get(event_type="backup_requested")
    assert event.subject_id == request.pk
    # The same confirmation again returns the first request, no second row.
    assert post(browser, action="confirm", key=key).status_code == 200
    assert BackupRequest.objects.count() == 1
    assert AuditEvent.objects.filter(event_type="backup_requested").count() == 1
    # While it waits, another preview is refused in the dialog, and the
    # button is greyed.
    refused = post(browser, action="preview")
    assert refused.status_code == 409
    assert '<div data-review-refusal><p data-backup-refused="busy">' in (
        refused.content.decode()
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        page = browser.get(PAGE).content.decode()
    assert 'disabled aria-describedby="backup-now-reason"' in page
    assert "and is waiting for the server" in page


def test_a_stale_sign_in_gets_the_google_step_up(auth_service, google, monkeypatch):
    """Answered in the dialog: the step-up form, returning to this page."""
    browser, _ = signed_in()
    monkeypatch.setattr(sessions, "FRESH_SECONDS", -1)
    for values in ({"action": "preview"}, {"action": "confirm", "key": str(uuid4())}):
        response = post(browser, **values)
        assert response.status_code == 403
        body = response.content.decode()
        assert "<div data-review-refusal>" in body
        assert 'data-backup-refused="reauthenticate"' in body
        assert 'id="backup-now"' not in body
        assert 'name="next" value="/admin/system/health/"' in body
    assert BackupRequest.objects.count() == 0


def test_only_an_administrator_may_request_a_backup(auth_service, google):
    """Staff cannot preview or confirm."""
    store = auth_service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=("staff",)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    assert post(browser, action="preview").status_code == 403
    assert post(browser, action="confirm", key=str(uuid4())).status_code == 403
    assert BackupRequest.objects.count() == 0


def test_stray_fields_are_refused_in_the_dialog_without_a_view(auth_service, google):
    """A malformed step is refused in the dialog. There is no Cancel step:
    the dialog's Cancel sends nothing (#1356)."""
    browser, _ = signed_in()
    views = AuditEvent.objects.filter(event_type="system_health_viewed").count()
    cancelled = post(browser, action="cancel")
    assert cancelled.status_code == 400
    assert 'data-backup-refused="invalid"' in cancelled.content.decode()
    refused = post(browser, action="preview", key="x")
    assert refused.status_code == 400
    assert 'data-backup-refused="invalid"' in refused.content.decode()
    assert post(browser, action="drop").status_code == 400
    # None of these is a page view.
    assert AuditEvent.objects.filter(event_type="system_health_viewed").count() == views
    assert BackupRequest.objects.count() == 0


def test_a_configuration_error_is_unavailable_not_an_unreadable_form(
    auth_service, google, monkeypatch
):
    """ConfigError is a ValueError, but answers 503, not the region's 400."""
    from parishkit.config import ConfigError
    from parishkit.stewardship import system_health

    def broken(request, store):
        """The configuration cannot be read."""
        raise ConfigError("unreadable configuration")

    browser, _ = signed_in()
    monkeypatch.setattr(system_health, "preview_backup", broken)
    response = post(browser, action="preview")
    assert response.status_code == 503
    assert 'data-backup-refused="invalid"' not in response.content.decode()
    assert BackupRequest.objects.count() == 0


def test_a_simultaneous_double_click_returns_the_first_request(auth_service, google):
    """Two confirmations with one key, in two connections: one request.

    Both pass the first lookup before either inserts, so the loser's insert
    waits on the guard's lock, is refused (23505) and re-reads the winner.
    """
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import connections
    from django.test import Client

    browser, _ = signed_in()
    key = key_of(post(browser, action="preview"))
    token = browser.cookies["pk_admin_csrf"].value
    barrier = Barrier(2, timeout=30)
    manager = BackupRequest.objects
    real_create = manager.create

    def create(**values):
        """Insert only once both confirmations have looked and found nothing."""
        barrier.wait()
        return real_create(**values)

    def confirm(client):
        """One confirmation in its own connection; its status and words."""
        try:
            response = client.post(
                URL,
                {"csrfmiddlewaretoken": token, "action": "confirm", "key": key},
                HTTP_ACCEPT="text/html",
            )
            return response.status_code, response.content.decode()
        finally:
            connections.close_all()

    other = Client()
    other.cookies = browser.cookies
    manager.create = create
    try:
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            ThreadPoolExecutor(2) as pool,
        ):
            answers = list(pool.map(confirm, (browser, other)))
    finally:
        del manager.create
    assert [status for status, _ in answers] == [200, 200]
    assert all('id="backup-now"' in body for _, body in answers)
    request = BackupRequest.objects.get()
    assert str(request.pk) == key
    assert AuditEvent.objects.filter(event_type="backup_requested").count() == 1


@pytest.mark.parametrize(
    ("message", "refused"),
    [
        (
            "A backup request requires fresh Administrator authentication",
            "reauthenticate",
        ),
        ("permission denied for table stewardship_backup_request", "denied"),
    ],
)
def test_only_the_fresh_sign_in_refusal_offers_the_step_up(
    auth_service, google, monkeypatch, caplog, message, refused
):
    """The guard's fresh sign-in refusal gets "Confirm it's you"; any other
    permission refusal (a missing grant) is said plainly and logged, since a
    new sign-in would not help."""
    from django.db import DatabaseError

    class Refusal(Exception):
        """A psycopg error's SQLSTATE, as Django wraps it."""

        sqlstate = "42501"

    def refuse(**values):
        """The insert the database refuses."""
        error = DatabaseError(message)
        error.__cause__ = Refusal(message)
        raise error

    browser, _ = signed_in()
    key = key_of(post(browser, action="preview"))
    monkeypatch.setattr(BackupRequest.objects, "create", refuse)
    answer = post(browser, action="confirm", key=key)
    assert answer.status_code == 403
    assert f'data-backup-refused="{refused}"' in answer.content.decode()
    assert not BackupRequest.objects.exists()
    assert ("task_failed" in caplog.text) == (refused == "denied")
