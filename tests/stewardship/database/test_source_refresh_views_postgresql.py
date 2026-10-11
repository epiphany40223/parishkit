"""The manual refresh page under the real web role: confirmed, keyed, coalesced."""

import re
from importlib import import_module
from uuid import uuid4

import pytest
from django.conf import settings
from django.db.models import F
from django.template.loader import render_to_string
from django.test import Client
from django.utils import timezone

from parishkit.stewardship.accounts import refresh_views
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy import Capability
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import views as job_views
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
from .test_source_families_postgresql import (
    publish,
    source_singletons,  # noqa: F401
)
from .test_source_requests_postgresql import claim
from .user_builders import add_rules

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/parish/parishsoft-refresh/"


def web():
    """Every browser request runs under the real restricted web role."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def post(browser, key):
    """Submit the confirmation with the genuine CSRF cookie."""
    return browser.post(
        URL,
        {
            "request_key": str(key),
            "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
        },
    )


def run_of(response):
    """The task root the confirmation remembers for this sign-in.

    The confirmation stays on the refresh page (navigation rule 5, #521):
    it redirects back to the page's run region, never to the task page, and
    remembers the run in the session, never in the URL.
    """
    assert response.status_code == 302, response.content
    assert response["Location"] == URL + "#refresh-run"
    return response.wsgi_request.session[refresh_views.RUN_KEY]


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
        # The page is its own sidebar entry under Parish data.
        assert f'<a href="{URL}" aria-current="page">' in body
        assert '<span aria-current="page">Refresh from ParishSoft</span>' in body
        key = uuid4()
        root = run_of(post(browser, key))
        # The same key replays; a new key while the run waits coalesces.
        assert run_of(post(browser, key)) == root
        assert run_of(post(browser, uuid4())) == root
        page = browser.get(URL).content.decode()
        assert "already waiting" in page
        # The page shows the remembered run's live status, polling its
        # passive status fragment, and links its Background task page; the
        # breadcrumb is the way back, so there is no Cancel link.
        assert 'data-live-status="task"' in page and "data-live-pending" in page
        assert (
            f'data-live-url="/admin/system/background/{root}/status/?view=refresh"'
            in page
        )
        assert f'<a href="/admin/system/background/{root}/">' in page
        assert ">Cancel<" not in page
        # The run's page and the task list both say which kind of refresh
        # this is, and the refresh page explains full vs quick updates.
        task_page = browser.get(f"/admin/system/background/{root}/").content.decode()
        assert "Full refresh" in task_page
        assert "records checked, not records changed" in task_page
        assert (
            "(Full refresh)"
            in browser.get("/admin/system/background/").content.decode()
        )
        assert "automatic quick updates" in browser.get(URL).content.decode()
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
                "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
            },
        )
        assert bad.status_code == 400
        stray = browser.post(
            URL,
            {
                "request_key": str(uuid4()),
                "extra": "1",
                "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
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


@pytest.mark.usefixtures("source_singletons")
def test_a_run_that_cannot_be_read_is_forgotten(auth_service, google):
    """A remembered run the reader can no longer read (here, one that does
    not exist) is dropped from the session, and the page shows no run."""
    publish(source())
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        requested = post(browser, uuid4())
    run_of(requested)
    # The sign-in's own session store, as the Admin session cookie names it.
    store = import_module(settings.SESSION_ENGINE).SessionStore(
        requested.wsgi_request.session.session_key
    )
    store[refresh_views.RUN_KEY] = str(uuid4())
    store.save()
    with web():
        page = browser.get(URL)
    assert page.status_code == 200
    body = page.content.decode()
    assert "Refresh now" in body and 'data-live-status="task"' not in body
    assert refresh_views.RUN_KEY not in page.wsgi_request.session


def requested(browser):
    """Request a refresh from the page; return the response and its run."""
    with web():
        response = post(browser, uuid4())
    return response, run_of(response)


def shown(browser):
    """The refresh page as this sign-in sees it, and its session."""
    with web():
        page = browser.get(URL)
    assert page.status_code == 200
    return page.content.decode(), page.wsgi_request.session


@pytest.mark.usefixtures("source_singletons")
def test_a_reader_without_background_work_forgets_the_run(
    auth_service, google, monkeypatch
):
    """A reader who may no longer read background work (the task read's
    403) sees the page without the run, which is forgotten."""
    publish(source())
    browser, login = signed_in()
    assert login.status_code == 302
    requested(browser)
    real = job_views.allows

    def without_background_work(principal, capability):
        """Every capability as before, except reading background work."""
        return capability != Capability.BACKGROUND_WORK and real(principal, capability)

    monkeypatch.setattr(job_views, "allows", without_background_work)
    body, session = shown(browser)
    assert "Refresh now" in body and 'data-live-status="task"' not in body
    assert refresh_views.RUN_KEY not in session


@pytest.mark.usefixtures("source_singletons")
def test_a_restore_review_keeps_the_run_for_later(auth_service, google, monkeypatch):
    """While a restore review makes the task read unavailable (503), the
    page shows no run but keeps it: the outage is not the run's fault."""
    publish(source())
    browser, login = signed_in()
    assert login.status_code == 302
    _, root = requested(browser)

    class UnderReview:
        """The task read's view of the system: a restore review is pending."""

        class objects:
            @staticmethod
            def first():
                """The one system configuration row, awaiting restore review."""
                return type("Row", (), {"restore_review_required": True})()

    with monkeypatch.context() as patched:
        patched.setattr(job_views, "SystemConfiguration", UnderReview)
        body, session = shown(browser)
    assert 'data-live-status="task"' not in body
    assert session[refresh_views.RUN_KEY] == root
    # Once the read is available again, the same run is shown.
    body, _ = shown(browser)
    assert f'data-live-url="/admin/system/background/{root}/status/?view=refresh"' in (
        body
    )


@pytest.mark.usefixtures("source_singletons")
def test_the_one_click_refresh_lands_on_the_refresh_page(auth_service, google):
    """Home's and the ParishSoft page's "Run a full refresh now" form posts
    its fields to the refresh action and lands on the page's run region,
    remembering the run, never on the task page (#521)."""
    publish(source())
    browser, login = signed_in()
    assert login.status_code == 302
    key = uuid4()
    form = render_to_string(
        "stewardship/full-refresh-action.html",
        {"can_refresh": True, "refresh_key": key, "csrf_token": "x"},
    )
    assert f'action="{URL}"' in form and f'value="{key}"' in form
    with web():
        response = browser.post(
            URL,
            {
                "request_key": str(key),
                "csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value,
            },
        )
    root = run_of(response)
    assert str(SourceRefreshRequest.objects.get().task_root_id) == root
    body, _ = shown(browser)
    assert f'<a href="/admin/system/background/{root}/">' in body


@pytest.mark.usefixtures("source_singletons")
def test_a_second_request_joins_the_remembered_run(auth_service, google):
    """Pressing Refresh now again, with the fresh key the page drew, while
    the run waits joins it: the session keeps the same run, the page shows
    it, and only one task exists. Its status poll takes the page's view."""
    publish(source())
    browser, login = signed_in()
    assert login.status_code == 302
    _, root = requested(browser)
    body, _ = shown(browser)
    fresh = re.search(r'name="request_key" value="([0-9a-f-]{36})"', body).group(1)
    with web():
        again = post(browser, fresh)
    assert run_of(again) == root
    body, session = shown(browser)
    assert session[refresh_views.RUN_KEY] == root
    assert f'<a href="/admin/system/background/{root}/">' in body
    assert TaskRun.objects.filter(task_type=TASK_TYPE).count() == 1
    assert SourceRefreshCommand.objects.count() == 2
    with web():
        status = browser.get(f"/admin/system/background/{root}/status/?view=refresh")
    assert status.status_code == 200
    assert 'data-live-status="task"' in status.content.decode()
