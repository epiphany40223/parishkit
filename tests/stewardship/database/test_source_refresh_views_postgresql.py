"""The manual refresh page under the real web role: confirmed, keyed, coalesced."""

from uuid import uuid4

import pytest

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
def test_only_an_administrator_may_request_a_refresh(auth_service, google):
    """Staff and anonymous callers are refused; malformed keys are refused."""
    publish(source())
    add_rules(auth_service.store, address("reader@example.org", ("staff",)))
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        assert browser.post(URL, {"request_key": str(uuid4())}).status_code == 403
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
