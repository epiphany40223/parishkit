"""The manual refresh page under the real web role: confirmed, keyed, coalesced."""

from uuid import uuid4

import pytest
from django.db.models import F
from django.test import Client
from django.utils import timezone

from parishkit.stewardship.accounts import refresh_views
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.refresh_models import (
    SourceRefreshCommand,
    SourceRefreshRequest,
)
from parishkit.stewardship.source.requests import TASK_TYPE

from ..policy_factory import address
from ..test_source_corpus import source
from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_chair_suggestions_postgresql import publish
from .test_source_families_postgresql import source_singletons  # noqa: F401
from .test_source_requests_postgresql import claim
from .test_user_views_postgresql import add_rules

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/source/refresh"


def web():
    """Every browser request runs under the real restricted web role."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def post(browser, key):
    """Submit the confirmation with the genuine CSRF cookie."""
    return browser.post(
        URL,
        {
            "request_key": str(key),
            "csrfmiddlewaretoken": browser.cookies["csrftoken"].value,
        },
    )


def run_of(response):
    """The task root the confirmation leads to."""
    assert response.status_code == 302, response.content
    assert response["Location"].startswith("/admin/background/task/")
    return response["Location"].rsplit("/", 1)[-1]


@pytest.mark.usefixtures("source_singletons")
def test_a_manual_refresh_is_confirmed_keyed_and_coalesced(auth_service, google):
    """One run per waiting window; the same key and a new key both lead to it."""
    publish(source())
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        page = browser.get(URL)
        assert page.status_code == 200
        body = page.content.decode()
        assert "Refresh now" in body and 'name="request_key"' in body
        assert "A refresh is running" not in body
        key = uuid4()
        root = run_of(post(browser, key))
        # The same key replays; a new key while the run waits coalesces.
        assert run_of(post(browser, key)) == root
        assert run_of(post(browser, uuid4())) == root
        assert "already waiting" in browser.get(URL).content.decode()
    assert TaskRun.objects.filter(task_type=TASK_TYPE).count() == 1
    request = SourceRefreshRequest.objects.get()
    assert (request.kind, str(request.task_root_id)) == ("full", root)
    commands = SourceRefreshCommand.objects.filter(request=request)
    assert commands.count() == 2 and {c.cause for c in commands} == {"manual"}
    assert {c.actor_id for c in commands} != {None}
    # Once that run is executing, a further request waits behind it.
    claim(type("Receipt", (), {"task_root_id": request.task_root_id})())
    with web():
        assert "A refresh is running" in browser.get(URL).content.decode()
        later = run_of(post(browser, uuid4()))
    assert later != root
    assert TaskRun.objects.filter(task_type=TASK_TYPE).count() == 2


@pytest.mark.usefixtures("source_singletons")
def test_a_session_ended_under_the_lock_records_no_command(
    auth_service, google, monkeypatch
):
    """The authorize callback denies a revoked session, on a new key and a replay."""
    publish(source())
    browser, login = signed_in()
    assert login.status_code == 302
    key = uuid4()
    with web():
        root = run_of(post(browser, key))
    assert SourceRefreshCommand.objects.count() == 1
    real = refresh_views.request_refresh
    reached = []

    def revoked_meanwhile(**values):
        """Revoke every live session after admission, before the domain's check."""
        reached.append(values["command_id"])
        for row in PortalSession.objects.filter(revoked_at__isnull=True):
            PortalSession.objects.filter(pk=row.pk, version=row.version).update(
                revoked_at=timezone.now(), version=F("version") + 1
            )
        return real(**values)

    monkeypatch.setattr(refresh_views, "request_refresh", revoked_meanwhile)
    fresh = uuid4()
    for attempt in (key, fresh):
        # Each attempt starts from a live session the page admits, so the
        # denial under the lock is the domain's own, for a replay and a new
        # key alike.
        session, login = signed_in()
        assert login.status_code == 302
        with web():
            assert post(session, attempt).status_code == 403
    assert reached == [key, fresh]
    assert SourceRefreshCommand.objects.count() == 1
    assert SourceRefreshRequest.objects.count() == 1
    assert str(SourceRefreshRequest.objects.get().task_root_id) == root


@pytest.mark.usefixtures("source_singletons")
def test_only_an_administrator_may_request_a_refresh(auth_service, google, monkeypatch):
    """Staff, anonymous and token-less callers are refused; so are malformed keys."""
    publish(source())
    add_rules(auth_service.store, address("reader@example.org", ("staff",)))
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        # A source without its configured organization is an outage to the
        # page, never a denial of this Administrator.
        with monkeypatch.context() as patched:

            def unconfigured(scope):
                """The domain's own refusal for a missing organization."""
                raise PermissionError("Source refresh requires its organization.")

            patched.setattr(
                "parishkit.stewardship.source.requests._organization", unconfigured
            )
            outage = post(browser, uuid4())
            assert outage.status_code == 503
            assert outage.json()["errors"][0]["code"] == "unavailable"
        assert not SourceRefreshRequest.objects.exists()
        # A missing CSRF token is refused before the route sees the request.
        assert browser.post(URL, {"request_key": str(uuid4())}).status_code == 403
        # An anonymous caller, with or without a CSRF token of its own, is
        # refused by admission and records nothing.
        anonymous = Client(enforce_csrf_checks=False)
        assert anonymous.get(URL).status_code == 403
        assert anonymous.post(URL, {"request_key": str(uuid4())}).status_code == 403
        assert not SourceRefreshRequest.objects.exists()
        bad = browser.post(
            URL,
            {
                "request_key": "not-a-key",
                "csrfmiddlewaretoken": browser.cookies["csrftoken"].value,
            },
        )
        assert bad.status_code == 400
        stray = browser.post(
            URL,
            {
                "request_key": str(uuid4()),
                "extra": "1",
                "csrfmiddlewaretoken": browser.cookies["csrftoken"].value,
            },
        )
        assert stray.status_code == 400
    google[0].update(email="reader@example.org", sub="reader-google-subject")
    reader, login = signed_in()
    assert login.status_code == 302
    with web():
        assert reader.get(URL).status_code == 403
        assert post(reader, uuid4()).status_code == 403
    assert not SourceRefreshRequest.objects.exists()
