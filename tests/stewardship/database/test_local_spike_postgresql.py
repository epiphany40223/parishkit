"""The spike check's Family run against the real views (#392 M3).

``run_family`` drives a Family through the five launch-day steps over HTTP.
Here its Browser talks to Django's test client instead of Caddy, so the
requests, the CSRF token it reads from the portal shell and the unchanged
submission it builds are checked against the real access link, session,
baseline, presence and submission code, under the web's own SQL login.
"""

from types import SimpleNamespace

import pytest
from django.test import Client

from parishkit.stewardship.campaigns.credential_models import (
    FamilySession,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.rehearsals import token_context
from parishkit.stewardship.local.spike import STEPS, Browser, run_family
from parishkit.stewardship.responses.models import Submission

from .response_builders import response_service  # noqa: F401 - fixture
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

# Headers the test client sets itself, or that only make sense over TLS: the
# client is plain HTTP on "testserver", so an https Origin would be refused.
SKIPPED = {"Host", "Cookie", "Origin", "Referer", "Content-Type"}


class ClientConnection:
    """An http.client-shaped connection that sends through Django's test client.

    The client keeps the cookies itself, so the Browser's own cookie header
    is left out; everything else it sends is passed on as request headers.
    """

    def __init__(self, client, address):
        self.client, self.address = client, address

    def request(self, method, path, body=None, headers=None):
        """Send one request through the client from this Family's address."""
        extra = {
            "HTTP_" + name.upper().replace("-", "_"): value
            for name, value in (headers or {}).items()
            if name not in SKIPPED
        }
        extra["REMOTE_ADDR"] = self.address
        if method == "GET":
            response = self.client.get(path, **extra)
        else:
            response = self.client.generic(
                method,
                path,
                body or b"",
                content_type=(headers or {}).get("Content-Type", ""),
                **extra,
            )
        self.response = response

    def getresponse(self):
        """The last answer, with the attributes the Browser reads."""
        response = self.response
        return SimpleNamespace(
            status=response.status_code,
            headers=SimpleNamespace(
                get=lambda name, default=None: response.get(name, default),
                get_all=lambda name: None,
            ),
            read=lambda: response.content,
        )

    def close(self):
        """Nothing to close."""


def family_token(harness):
    """The rehearsal link token of the harness's one Family."""
    credential = RehearsalCredential.objects.get()
    return harness.rings.private.decrypt(
        credential.token_ciphertext, context=token_context(credential.pk)
    ).decode()


def browser(address="192.0.2.10"):
    """A Browser whose connection is a fresh test client."""
    client = Client(enforce_csrf_checks=True)
    return Browser(None, connection_factory=lambda: ClientConnection(client, address))


def test_a_family_completes_every_step_and_its_submission_is_accepted(
    response_service,  # noqa: F811 - fixture
):
    """The link, shell, form, presence and unchanged submission all succeed."""
    token = family_token(response_service)
    before = FamilySession.objects.count()
    with web_login():
        results = run_family(browser(), token)
    assert {step: results[step][1] for step in STEPS} == dict.fromkeys(STEPS, "ok")
    assert all(results[step][0] >= 0 for step in STEPS)
    assert FamilySession.objects.count() > before
    submission = Submission.objects.get()
    assert submission.family.campaign_id == response_service.campaign.pk


def test_the_per_source_bucket_names_its_refusals_limited(
    response_service,  # noqa: F811 - fixture
):
    """Past the access burst from one source, the access step is limited.

    The bucket refills at two sign-ins a second, faster than a full Family
    run, so the burst is spent with bare link requests first.
    """
    token = family_token(response_service)
    statuses = []
    with web_login():
        client = browser("192.0.2.20")
        for _ in range(60):
            statuses.append(client.request("GET", f"/access/{token}")[1])
            if statuses[-1] == 429:
                break
        assert statuses[-1] == 429 and set(statuses[:-1]) == {302}
        assert run_family(browser("192.0.2.20"), token)["access"][1] == "limited"
        # Another source is not affected.
        assert run_family(browser("192.0.2.21"), token)["access"][1] == "ok"
