"""Review regressions exercise real SQL, signed sessions and pre-header admission."""

import socket
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.utils import timezone

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import CryptographicError, Key
from parishkit.stewardship.accounts.family_authentication import FamilyRuntime
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
from parishkit.stewardship.accounts.secret_requests import stage_secret_request
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.campaigns.credential_models import (
    RehearsalCodeFingerprint,
    RehearsalCodeReservation,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.models import Campaign

from .auth_builders import signed_in
from .campaign_builders import add_draft, restored_runtime
from .credential_builders import keys, populate
from .test_family_auth_postgresql import family_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def report_outcomes():
    """Read safe outcome/count evidence after the report guard has closed."""
    return list(
        AuditContext.objects.filter(event__event_type="family_codes_viewed")
        .order_by("event__created_at")
        .values_list("context", flat=True)
    )


@pytest.fixture
def report(auth_service, google, settings):
    """Provide a real signed-in Admin and a small code report with a real socket."""
    _, row, _ = add_draft(auth_service.store, auth_service.store.active(), uuid4())
    campaign = Campaign.objects.get(pk=UUID(row["id"]))
    ring = keys()
    populate(
        campaign,
        ring,
        [FamilyStatus(index, True, True, True, True) for index in (1, 2)],
    )
    settings.STEWARDSHIP_FAMILY_RUNTIME = FamilyRuntime(
        auth_service.store, auth_service.limiter, ring.general, ring.mac, ring.public
    )
    browser, response = signed_in()
    assert response.status_code == 302
    server, client = socket.socketpair()
    try:
        yield browser, f"/admin/campaign/{campaign.pk}/family-codes", server
    finally:
        server.close()
        client.close()


def test_report_decryption_failure_returns_503_before_headers(report, monkeypatch):
    """A busy key inventory cannot become a truncated successful report."""
    from parishkit.stewardship.accounts import code_reports

    @contextmanager
    def busy(*args):
        raise CryptographicError("Synthetic private key error")
        yield  # pragma: no cover

    monkeypatch.setattr(code_reports, "key_set_lock", busy)
    browser, path, server = report
    response = browser.get(path, **{"gunicorn.socket": server})
    assert response.status_code == 503 and response["Retry-After"] == "5"
    assert not response.streaming
    assert b"private key" not in response.content
    assert report_outcomes() == [
        {"outcome": "started"},
        {"outcome": "failed", "count": 0},
    ]


def test_abandoned_report_records_server_failure_after_guard_release(report):
    """Prepared bytes are not a successful stream when the caller closes early."""
    browser, path, server = report
    response = browser.get(path, **{"gunicorn.socket": server})
    assert response.status_code == 200
    response.close()
    response.close()
    assert report_outcomes() == [
        {"outcome": "started"},
        {"outcome": "failed", "count": 2},
    ]


def test_report_reauthorizes_after_outer_admission(report, monkeypatch):
    """Revoke between the view check and guard entry, before any code is decrypted."""
    from parishkit.stewardship.accounts import code_reports
    from parishkit.stewardship.accounts.sessions import end_admin

    original = code_reports.campaign_response

    def after_revoke(request, *args, **kwargs):
        end_admin(request)
        return original(request, *args, **kwargs)

    monkeypatch.setattr(code_reports, "campaign_response", after_revoke)
    browser, path, server = report
    response = browser.get(path, **{"gunicorn.socket": server})
    assert response.status_code == 503
    assert PortalSession.objects.get().revoked_at is not None
    assert report_outcomes() == [
        {"outcome": "started"},
        {"outcome": "failed", "count": 0},
    ]


@pytest.mark.parametrize(
    "query", ["?page=0", "?size=101", "?page=1&page=2", "?unknown=x"]
)
def test_report_validation_is_accessible_html(report, query):
    """Invalid document navigation returns the full HTML retry experience."""
    browser, path, server = report
    response = browser.get(path + query, **{"gunicorn.socket": server})
    assert response.status_code == 400
    assert response["Content-Type"].startswith("text/html")
    assert b"<h1>" in response.content


def test_report_restore_gate_never_streams_codes(report):
    """Restore routes the Admin into maintenance before opening a code report."""
    browser, path, server = report
    with restored_runtime(timezone.now() - timedelta(hours=1)):
        response = browser.get(path, **{"gunicorn.socket": server})
        assert response.status_code == 302
        assert response["Location"] == "/admin/maintenance"


def test_report_pagination_uses_bounded_windows_and_navigation(report):
    """Each page contains one actual Family and offers only applicable navigation."""
    browser, path, server = report
    for page in (1, 2):
        response = browser.get(
            path + f"?page={page}&size=1", **{"gunicorn.socket": server}
        )
        assert response.status_code == 200
        body = b"".join(response.streaming_content).decode()
        response.close()
        assert body.count('scope="row"') == 1
        assert f'<th scope="row">{page}</th>' in body
        assert ("Next page" in body) == (page == 1)
        assert ("Previous page" in body) == (page == 2)


def test_admin_messages_stay_in_the_admin_database_session(report, monkeypatch):
    """Django messages never create a root-scoped identity-bearing message cookie."""
    from parishkit.stewardship.accounts import authentication

    original = authentication.render

    def notify(request, *args, **kwargs):
        messages.info(request, "Synthetic private Admin message")
        return original(request, *args, **kwargs)

    monkeypatch.setattr(authentication, "render", notify)
    response = report[0].get("/admin/")
    assert response.status_code == 200
    assert "messages" not in response.cookies and "pk_family" not in response.cookies
    assert (
        "Synthetic private"
        in PortalSession.objects.get().session.get_decoded()["_messages"]
    )


@pytest.mark.parametrize("consumers", [("web",), ("worker",), ("web", "worker")])
def test_sealed_credential_intake_requires_complete_consumers(consumers):
    """Neither the typed intake nor raw SQL can omit a mounting service."""
    identifier, actor = uuid4(), uuid4()
    intent = dict(
        request_id=identifier,
        target="general_encryption",
        staging_reference=uuid4(),
        actor_id=actor,
        reauthenticated_at=timezone.now() - timedelta(seconds=5),
        expires_at=timezone.now() + timedelta(minutes=5),
        expected_fingerprint=None,
        correlation_id=uuid4(),
        required_consumers=consumers,
        sealed_candidate=PrivateHandoff(
            "general_encryption", Key("handoff", "active", b"h" * 32)
        )
        .public()
        .seal(identifier, b"synthetic"),
        candidate_fingerprint="a" * 64,
    )
    if len(consumers) == 2:
        assert stage_secret_request(**intent).state == "staged"
        return
    with pytest.raises(ConfigError, match="inventory"):
        stage_secret_request(**intent)
    with pytest.raises(IntegrityError, match="must be complete"), transaction.atomic():
        SecretReplacementRequest.objects.create(
            target=intent["target"],
            actor_id=actor,
            requested_by_id=actor,
            staging_reference=intent["staging_reference"],
            reauthenticated_at=intent["reauthenticated_at"],
            expires_at=intent["expires_at"],
            required_consumers=list(consumers),
        )


@pytest.mark.parametrize("values", [{"algorithm": "other"}, {"digest": "A" * 64}])
def test_rehearsal_mac_format_is_enforced_by_sql(family_service, values):  # noqa: F811
    """Raw inserts cannot create unusable lookups or collision reservations."""
    credential = RehearsalCredential.objects.get()
    common = {"key_id": "other", "digest": "a" * 64, **values}
    with (
        pytest.raises(IntegrityError, match="rehearsal_code_mac_format"),
        transaction.atomic(),
    ):
        RehearsalCodeFingerprint.objects.create(
            credential=credential, epoch=credential.epoch, **common
        )
    with (
        pytest.raises(IntegrityError, match="rehearsal_reservation_format"),
        transaction.atomic(),
    ):
        RehearsalCodeReservation.objects.create(
            campaign=family_service.campaign, **common
        )
