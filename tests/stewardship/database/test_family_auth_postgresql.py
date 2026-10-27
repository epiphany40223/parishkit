"""Real Family HTTP exchange, disjoint cookies, current epochs and idle controls."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, transaction
from django.db.models import F
from django.test import Client

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.family_authentication import FamilyRuntime
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    FamilySession,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.family_identity import (
    code_context as production_code_context,
)
from parishkit.stewardship.campaigns.lifecycle import CampaignWorkKind
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.rehearsals import (
    code_context,
    invalidate_rehearsal,
    prepare_rehearsals,
    token_context,
)

from .auth_builders import unguarded
from .campaign_builders import add_draft, campaign_clock
from .credential_builders import keys, populate

pytestmark = pytest.mark.django_db(transaction=True)


@dataclass
class FamilyHarness:
    """No source/provider credentials: only a real campaign and synthetic keyring."""

    campaign: object
    rings: object
    service: FamilyRuntime
    code: str
    token: str


@pytest.fixture
def family_service(auth_service, settings):
    """Install the Family overlay through actual population and epoch services."""
    _, row, _ = add_draft(auth_service.store, auth_service.store.active(), uuid4())
    campaign = Campaign.objects.get(pk=UUID(row["id"]))
    ring = keys()
    populate(campaign, ring)
    service = FamilyRuntime(
        auth_service.store, auth_service.limiter, ring.general, ring.mac, ring.public
    )
    settings.STEWARDSHIP_FAMILY_RUNTIME = service
    with campaign_clock(campaign.active_configuration.starts_at):
        prepare_rehearsals(
            campaign_id=campaign.pk,
            family_ids=list(FamilyCampaign.objects.values_list("pk", flat=True)),
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
            purpose=CampaignWorkKind.REHEARSAL,
            admit=lambda *args: True,
        )
        credential = RehearsalCredential.objects.get()
        yield FamilyHarness(
            campaign,
            ring,
            service,
            ring.general.decrypt(
                credential.code_ciphertext, context=code_context(credential.pk)
            ).decode(),
            ring.private.decrypt(
                credential.token_ciphertext, context=token_context(credential.pk)
            ).decode(),
        )


def login(code, client=None):
    """Exercise the rendered CSRF form and actual cookie namespace."""
    client = client or Client(enforce_csrf_checks=True)
    assert client.get("/").status_code == 200
    response = client.post(
        "/",
        {"code": code, "csrfmiddlewaretoken": client.cookies["pk_family_csrf"].value},
    )
    return client, response


def test_session_epoch_cannot_be_rebound_by_direct_update(family_service):
    """Even an otherwise valid version advance cannot revive a restored session."""
    _, response = login(family_service.code)
    assert response.status_code == 302
    row = FamilySession.objects.get()
    with pytest.raises(IntegrityError, match="immutable"), transaction.atomic():
        FamilySession.objects.filter(pk=row.pk).update(
            credential_epoch=uuid4(), version=F("version") + 1
        )
    row.refresh_from_db()
    assert row.version == 1


def test_multipart_keepalive_is_a_bad_request_not_a_server_error(family_service):
    """CSRF may consume multipart input before the empty-body activity boundary."""
    client, response = login(family_service.code)
    assert response.status_code == 302
    response = client.post(
        "/family/keepalive",
        {"csrfmiddlewaretoken": client.cookies["pk_family_csrf"].value, "claim": "x"},
    )
    assert response.status_code == 400


def test_family_login_reuses_only_the_fresh_locked_scope(family_service, monkeypatch):
    """One unlocked lookup and one lock-protected scope suffice for session minting."""
    from parishkit.stewardship.accounts import family_authentication as auth

    original, calls = auth._scope, []

    def observed(service, **options):
        calls.append(True)
        return original(service, **options)

    client = Client(enforce_csrf_checks=True)
    assert client.get("/").status_code == 200
    monkeypatch.setattr(auth, "_scope", observed)
    response = client.post(
        "/",
        {
            "code": family_service.code,
            "csrfmiddlewaretoken": client.cookies["pk_family_csrf"].value,
        },
    )
    assert response.status_code == 302
    assert len(calls) == 2


def test_ordered_family_cleanup_preserves_audit_attribution(family_service):
    from django.contrib.sessions.models import Session

    from parishkit.stewardship.accounts.sessions import cleanup_family_sessions
    from parishkit.stewardship.audit.models import AuditEvent

    client, response = login(family_service.code)
    assert response.status_code == 302
    row = FamilySession.objects.get()
    assert cleanup_family_sessions() == 0
    response = client.post(
        "/family/logout",
        {
            "csrfmiddlewaretoken": client.cookies["pk_family_csrf"].value,
        },
    )
    assert response.status_code == 302
    assert cleanup_family_sessions() == 1
    assert not FamilySession.objects.filter(pk=row.pk).exists()
    assert not Session.objects.filter(pk=row.session_id).exists()
    assert AuditEvent.objects.filter(subject_id=row.pk).exists()


def test_invalid_link_audit_is_bounded_and_attempts_stay_ephemeral(family_service):
    """ARC-05 audit policy: sample failures, but retain each successful login."""
    from parishkit.stewardship.audit.models import AuditContext, AuditEvent

    for index in range(10):
        response = Client().get(
            f"/access/invalid-{index}", REMOTE_ADDR=f"192.0.2.{index + 1}"
        )
        assert response.status_code == 403
    limiter = family_service.service.limiter
    assert limiter.client.zcard(limiter.namespace + ":aggregate:family:attempts") == 10
    event = AuditEvent.objects.get(event_type="family_link_invalid")
    assert AuditContext.objects.get(event=event).context == {"outcome": "denied"}
    assert event.actor_id is None and event.subject_id is None
    assert event.campaign_reference is None

    # Failure sampling must never suppress successful exchanges, even when they
    # reuse the same valid link immediately after a deployment-wide sample.
    for _ in range(2):
        assert Client().get("/access/" + family_service.token).status_code == 302
    sessions = FamilySession.objects.all()
    assert sessions.count() == 2
    assert set(
        AuditEvent.objects.filter(event_type="family_login").values_list(
            "subject_id", "actor_id"
        )
    ) == set(sessions.values_list("pk", "family_id"))
    assert AuditEvent.objects.filter(event_type="family_link_invalid").count() == 1


def test_distributed_manual_failures_have_bounded_durable_audit(family_service):
    """Many public sources retain per-attempt ephemeral counts, not permanent rows."""
    from parishkit.stewardship.audit.models import AuditEvent

    for index in range(20):
        response = Client().post(
            "/", {"code": "ZZZZZZZZ"}, REMOTE_ADDR=f"192.0.2.{index + 1}"
        )
        assert response.status_code == 403
    assert AuditEvent.objects.filter(event_type="family_login_failed").count() == 1
    limiter = family_service.service.limiter
    assert limiter.client.zcard(limiter.namespace + ":aggregate:family:attempts") == 20


def test_code_exchange_and_token_link_use_clean_isolated_session(family_service):
    client, response = login(family_service.code.lower())
    assert response.status_code == 302 and response["Location"] == "/family/"
    assert "pk_admin" not in response.cookies
    assert response.cookies["pk_family"]["httponly"]
    assert response.cookies["pk_family"]["samesite"] == "Lax"
    assert client.get("/family/").status_code == 200
    first = client.cookies["pk_family"].value
    previous = FamilySession.objects.get(revoked_at__isnull=True)
    response = client.get("/access/" + family_service.token)
    assert response.status_code == 302 and response["Location"] == "/family/"
    assert client.cookies["pk_family"].value != first
    assert response["Referrer-Policy"] == "no-referrer"
    assert FamilySession.objects.filter(revoked_at__isnull=True).count() == 1
    assert (
        AuditEvent.objects.filter(
            event_type="family_session_ended", subject_id=previous.pk
        ).count()
        == 1
    )
    row = FamilySession.objects.get(revoked_at__isnull=True)
    assert row.expires_at - row.authenticated_at == timedelta(hours=4)
    assert set(row.session.get_decoded()) == {"family", "_session_expiry"}


def test_production_code_never_authenticates_testing(family_service):
    family = FamilyCampaign.objects.get()
    code = family_service.rings.general.decrypt(
        family.code_ciphertext, context=production_code_context(family.pk)
    ).decode()
    _, response = login(code)
    assert response.status_code == 403
    assert not FamilySession.objects.exists()


@pytest.mark.parametrize("visit_after", [False, True])
def test_invalidation_ends_testing_sessions_before_sensitive_cleanup(
    family_service, visit_after
):
    from django.contrib.sessions.models import Session

    from parishkit.stewardship.campaigns.rehearsals import cleanup_rehearsal

    client, _ = login(family_service.code)
    session = FamilySession.objects.get()
    assert client.get("/family/").status_code == 200
    invalidate_rehearsal(
        campaign_id=family_service.campaign.pk, admit=lambda *args: True
    )
    assert RehearsalCredential.objects.exists()
    if visit_after:
        assert client.get("/family/").status_code == 302
        assert FamilySession.objects.get().revoked_at is not None
    assert client.get("/access/" + family_service.token).status_code == 403
    # A batch of one removes one session, one credential and the Family's
    # Testing engagement row (#477) independently; parent session cleanup
    # follows deletion of its PROTECT metadata child.
    assert cleanup_rehearsal(session.rehearsal_epoch_id, batch_size=1) == 3
    assert not FamilySession.objects.filter(pk=session.pk).exists()
    assert not Session.objects.filter(pk=session.session_id).exists()
    assert cleanup_rehearsal(session.rehearsal_epoch_id, batch_size=1) == 0
    assert (
        AuditEvent.objects.filter(
            event_type="family_session_ended", subject_id=session.pk
        ).count()
        == 1
    )


def backdate_session(minutes):
    """Age the one Family session instead of moving the clock forward.

    The session guard refuses activity recorded in the future (#306), so a
    later keepalive is modelled by an earlier sign-in, written as the owner.
    """
    shift = timedelta(minutes=minutes)
    with unguarded():
        FamilySession.objects.update(
            authenticated_at=F("authenticated_at") - shift,
            last_activity_at=F("last_activity_at") - shift,
            expires_at=F("expires_at") - shift,
        )
    return FamilySession.objects.get()


def test_keepalive_is_empty_csrf_protected_rate_bounded_and_passive(family_service):
    client, _ = login(family_service.code)
    row = backdate_session(10)
    original = row.last_activity_at
    assert (
        client.post(
            "/family/keepalive", b"", content_type="application/json"
        ).status_code
        == 403
    )
    csrf = client.cookies["pk_family_csrf"].value
    assert (
        client.post(
            "/family/keepalive",
            b"{}",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        ).status_code
        == 400
    )
    response = client.post(
        "/family/keepalive", b"", content_type="application/json", HTTP_X_CSRFTOKEN=csrf
    )
    assert response.status_code == 200
    row.refresh_from_db()
    assert row.last_activity_at >= original + timedelta(minutes=10)
    version = row.version
    assert (
        client.post(
            "/family/keepalive",
            b"",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        ).status_code
        == 200
    )
    row.refresh_from_db()
    assert row.version == version
    assert row.expires_at == row.authenticated_at + timedelta(hours=4)


def test_keepalive_leaves_the_campaign_row_alone_during_maintenance(
    family_service, monkeypatch
):
    """While closed, keepalive refreshes only the session, not FamilyCampaign."""
    from parishkit.stewardship.accounts import family_maintenance
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

    client, _ = login(family_service.code)
    row = backdate_session(10)
    before = FamilyCampaign.objects.values("last_activity_at", "version").get(
        pk=row.family_id
    )
    original = row.last_activity_at
    monkeypatch.setattr(
        family_maintenance,
        "current_state",
        lambda **_: family_maintenance.MaintenanceState(closed=True),
    )
    response = client.post(
        "/family/keepalive",
        b"",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=client.cookies["pk_family_csrf"].value,
    )
    assert response.status_code == 200
    row.refresh_from_db()
    assert row.last_activity_at >= original + timedelta(minutes=10)
    assert (
        FamilyCampaign.objects.values("last_activity_at", "version").get(
            pk=row.family_id
        )
        == before
    )


def test_idle_and_absolute_deadlines_cannot_be_extended(family_service, monkeypatch):
    from parishkit.stewardship.accounts import family_authentication

    client, _ = login(family_service.code)
    row = FamilySession.objects.get()
    monkeypatch.setattr(
        family_authentication,
        "database_now",
        lambda: row.last_activity_at + timedelta(hours=1),
    )
    assert client.get("/family/").status_code == 302
    row.refresh_from_db()
    assert row.revoked_at is not None


def test_pair_failures_do_not_lock_reactivated_valid_code(family_service):
    populate(
        family_service.campaign,
        family_service.rings,
        [FamilyStatus(1, False, False, False, False, "inactive", "ineligible")],
        generation=2,
    )
    client = Client(enforce_csrf_checks=True)
    for _ in range(6):
        _, response = login(family_service.code, client)
    assert response.status_code == 429
    populate(family_service.campaign, family_service.rings, generation=3)
    _, response = login(family_service.code, client)
    assert response.status_code == 302


def test_pair_counter_groups_ipv6_sources_by_64(family_service):
    """#383: alternating addresses in one /64 share one code/source pair budget.

    Three failures from each of two addresses would stay under the five-failure
    pair limit if keyed per address; grouped, the sixth is limited. The same
    candidate from another /64 is still only an ordinary failure.
    """
    first = Client(enforce_csrf_checks=True, REMOTE_ADDR="2001:db8:1:2::a")
    second = Client(enforce_csrf_checks=True, REMOTE_ADDR="2001:db8:1:2::b")
    for index in range(6):
        _, response = login("ILOOOOOO", (first, second)[index % 2])
    assert response.status_code == 429
    elsewhere = Client(enforce_csrf_checks=True, REMOTE_ADDR="2001:db8:1:3::a")
    assert login("ILOOOOOO", elsewhere)[1].status_code == 403
    limiter = family_service.service.limiter
    assert (
        len(list(limiter.client.scan_iter(limiter.namespace + ":window:family_pair:*")))
        == 2
    )


def test_invalid_length_counts_only_ip_and_ascii_ilo_counts_pair(family_service):
    client = Client(enforce_csrf_checks=True)
    assert login("bad", client)[1].status_code == 403
    limiter = family_service.service.limiter
    assert not list(
        limiter.client.scan_iter(limiter.namespace + ":window:family_pair:*")
    )
    assert login("ILOOOOOO", client)[1].status_code == 403
    assert (
        len(list(limiter.client.scan_iter(limiter.namespace + ":window:family_pair:*")))
        == 1
    )


def test_logout_does_not_touch_admin_cookie(family_service):
    client, _ = login(family_service.code)
    client.cookies["pk_admin"] = "separate-admin-cookie"
    response = client.post(
        "/family/logout",
        {"csrfmiddlewaretoken": client.cookies["pk_family_csrf"].value},
    )
    assert response.status_code == 302
    assert response.cookies["pk_family"]["max-age"] == 0
    assert "pk_admin" not in response.cookies
    assert FamilySession.objects.get().revoked_at is not None


def test_restored_deployment_epoch_invalidates_existing_family_session(family_service):
    from parishkit.stewardship.campaigns.credential_models import (
        DeploymentCredentialState,
    )

    client, _ = login(family_service.code)
    DeploymentCredentialState.objects.update(
        family_link_epoch=uuid4(), version=F("version") + 1
    )
    assert client.get("/family/").status_code == 302


# Fixed denial texts. The code and link sentences quote the parishioner-portal
# specification; the outage sentence is the generic temporary-unavailability
# response shared by rate limiting and limiter/configuration outages.
CODE_DENIAL = b"This Family code cannot be found or used."
LINK_DENIAL = b"This secure Family link cannot be found or used."
UNAVAILABLE = b"Sign-in is temporarily unavailable. Please try again later."
DENIAL_TEXTS = (CODE_DENIAL, LINK_DENIAL, UNAVAILABLE, b"Sign-in is unavailable.")


def denial_kind(response):
    """Return the single fixed denial text a response renders, and its retry link."""
    shown = [text for text in DENIAL_TEXTS if text in response.content]
    assert len(shown) == 1, shown
    assert b'href="/"' in response.content
    assert response["Cache-Control"] == "no-store"
    return shown[0]


def test_every_rejected_code_reason_renders_one_identical_code_page(family_service):
    """Unknown, malformed, wrong-mode and ineligible codes are indistinct.

    Each attempt uses its own source address so no limiter budget is reached;
    the page must name the code-denial kind and nothing about the reason.
    """
    production = FamilyCampaign.objects.get()
    production_code = family_service.rings.general.decrypt(
        production.code_ciphertext, context=production_code_context(production.pk)
    ).decode()
    addresses = iter(f"192.0.2.{index}" for index in range(1, 50))

    def attempt(code):
        """Submit one manual code from a fresh source address."""
        return Client().post("/", {"code": code}, REMOTE_ADDR=next(addresses))

    responses = [
        attempt("ZZZZZZZZ"),  # unknown
        attempt("bad"),  # malformed, never looked up
        attempt(production_code),  # Production code during Testing
    ]
    populate(
        family_service.campaign,
        family_service.rings,
        [FamilyStatus(1, False, False, False, False, "inactive", "ineligible")],
        generation=2,
    )
    responses.append(attempt(family_service.code))  # inactive/ineligible Family
    # A code after close gets the ended page instead; see the #1793 tests below.
    assert {response.status_code for response in responses} == {403}
    assert {denial_kind(response) for response in responses} == {CODE_DENIAL}
    assert b"Try another Family code" in responses[0].content
    assert len({response.content for response in responses}) == 1
    assert not FamilySession.objects.exists()


def test_every_rejected_link_reason_renders_one_identical_link_page(family_service):
    """Unknown and invalidated links share one page pointing to manual entry."""
    unknown = Client().get("/access/unknown-token", REMOTE_ADDR="192.0.2.10")
    invalidate_rehearsal(
        campaign_id=family_service.campaign.pk, admit=lambda *args: True
    )
    revoked = Client().get("/access/" + family_service.token, REMOTE_ADDR="192.0.2.11")
    for response in (unknown, revoked):
        assert response.status_code == 403
        assert denial_kind(response) == LINK_DENIAL
        assert b"Enter your Family code instead" in response.content
    assert unknown.content == revoked.content


ENDED = b"This parish&#x27;s census / stewardship campaign has ended."
ENDED_HEADING = b"<h1>Sorry, the campaign is over</h1>"


def _after_close(service):
    """A clock one hour past the campaign's end instant."""
    return campaign_clock(
        service.campaign.active_configuration.ends_at + timedelta(hours=1)
    )


def test_after_close_every_link_and_code_gets_one_identical_ended_page(
    family_service, monkeypatch
):
    """#1793: after close, valid and invalid credentials get the same ended page.

    The decision is campaign-wide, so a valid link, an unknown link, a
    malformed link, a valid code, an unknown code and a malformed code all
    receive byte-identical bodies, the unchanged 403 status and ``no-store``.
    Failure accounting runs once per attempt, exactly as for any rejection.
    """
    from parishkit.stewardship.accounts import family_authentication

    limiter = family_service.service.limiter
    failed, rejections = [], []
    real_failed = limiter.failed
    monkeypatch.setattr(
        limiter, "failed", lambda *a, **k: failed.append(1) or real_failed(*a, **k)
    )
    monkeypatch.setattr(
        family_authentication, "record_link_rejection", lambda: rejections.append(1)
    )
    addresses = iter(f"192.0.2.{index}" for index in range(100, 150))

    def link(token):
        """Follow one emailed link from a fresh source address."""
        return Client().get("/access/" + token, REMOTE_ADDR=next(addresses))

    def code(value):
        """Submit one manual code from a fresh source address."""
        return Client().post("/", {"code": value}, REMOTE_ADDR=next(addresses))

    with _after_close(family_service):
        root = Client().get("/")
        responses = [
            link(family_service.token),  # valid link
            link("test.unknown-token"),  # unknown link
            link("x"),  # malformed link
            code(family_service.code),  # valid code
            code("ZZZZZZZZ"),  # unknown code
            code("bad"),  # malformed code, never looked up
        ]
    assert root.status_code == 200 and ENDED in root.content
    assert {response.status_code for response in responses} == {403}
    assert {response["Cache-Control"] for response in responses} == {"no-store"}
    assert len({response.content for response in responses}) == 1
    # No credential path may set a cookie or header the others do not.
    assert {tuple(sorted(response.cookies)) for response in responses} == {()}
    assert len({response["Content-Type"] for response in responses}) == 1
    assert not [
        response for response in responses if response.has_header("Retry-After")
    ]
    page = responses[0].content
    assert ENDED_HEADING in page and ENDED in page
    assert b'href="/">Check again' in page
    assert not [text for text in DENIAL_TEXTS if text in page]
    # The same page / shows, rendered with the same template and text.
    assert page == root.content
    assert len(failed) == 6 and len(rejections) == 3
    assert not FamilySession.objects.exists()


def test_after_close_throttled_and_maintenance_keep_their_own_pages(
    family_service, monkeypatch
):
    """Throttling (429) and the maintenance gate (503) still take precedence.

    The code case reaches the failure-time limit check after the lookup, so
    the ended page can never replace a 429 that the failure itself caused.
    """
    limiter = family_service.service.limiter
    with _after_close(family_service):
        _throttled(monkeypatch, family_service)
        throttled = Client().get("/access/" + family_service.token)
        monkeypatch.undo()
        monkeypatch.setattr(
            limiter, "counters", lambda counters, *, failure=False: 30 * failure
        )
        code_throttled = Client().post("/", {"code": family_service.code})
        monkeypatch.undo()
        _closed(monkeypatch)
        maintenance = Client().get("/access/" + family_service.token)
    assert throttled.status_code == code_throttled.status_code == 429
    assert denial_kind(throttled) == denial_kind(code_throttled) == UNAVAILABLE
    assert maintenance.status_code == 503
    for response in (throttled, code_throttled, maintenance):
        assert ENDED not in response.content


def test_before_start_links_and_codes_keep_the_generic_denial(family_service):
    """Only the ended page replaces the denial; before start stays generic."""
    starts_at = family_service.campaign.active_configuration.starts_at
    with campaign_clock(starts_at - timedelta(hours=1)):
        link = Client().get("/access/" + family_service.token)
        code = Client().post("/", {"code": family_service.code})
    assert link.status_code == code.status_code == 403
    assert denial_kind(link) == LINK_DENIAL
    assert denial_kind(code) == CODE_DENIAL


def test_a_campaign_being_prepared_keeps_the_generic_denial(family_service):
    """A go-live gate inside the interval is not the end: links stay generic."""
    from parishkit.stewardship.campaigns.credential_models import (
        CampaignCredentialState,
    )

    with unguarded():
        CampaignCredentialState.objects.filter(campaign=family_service.campaign).update(
            go_live_gate=True
        )
    response = Client().get("/access/" + family_service.token)
    assert response.status_code == 403
    assert denial_kind(response) == LINK_DENIAL


def test_failed_ended_check_falls_back_to_the_generic_denial(
    family_service, monkeypatch
):
    """An optional campaign read that fails never turns a denial into an error."""
    from django.db import DatabaseError

    from parishkit.stewardship.responses import availability

    def broken(service):
        """Simulate the campaign-state read failing."""
        raise DatabaseError("synthetic")

    monkeypatch.setattr(availability, "unavailable_page", broken)
    with _after_close(family_service):
        response = Client().get("/access/" + family_service.token)
    assert response.status_code == 403
    assert denial_kind(response) == LINK_DENIAL


def test_rate_limit_and_limiter_outage_render_temporary_unavailability(
    family_service, monkeypatch
):
    """Throttling and outage keep their statuses but never claim the code is bad."""
    from parishkit.stewardship.accounts.limiting import LimiterUnavailable

    client = Client(enforce_csrf_checks=True)
    for _ in range(6):
        _, limited = login("ZZZZZZZZ", client)
    assert limited.status_code == 429 and limited["Retry-After"]
    _, still_limited = login("ZZZZZZZZ", client)
    assert still_limited.status_code == 429

    def unavailable(*args, **kwargs):
        """Simulate the Valkey limiter store failing before credential lookup."""
        raise LimiterUnavailable("synthetic-private-diagnostic")

    monkeypatch.setattr(family_service.service.limiter, "counters", unavailable)
    outage = Client().post("/", {"code": "ZZZZZZZZ"}, REMOTE_ADDR="192.0.2.20")
    assert outage.status_code == 503 and outage["Retry-After"] == "5"
    assert b"synthetic-private-diagnostic" not in outage.content
    for response in (limited, still_limited, outage):
        assert denial_kind(response) == UNAVAILABLE
        assert b"Try signing in again" in response.content
    assert limited.content == still_limited.content == outage.content


@pytest.fixture
def debug_log(monkeypatch, caplog):
    """Formatted debug-logging output, as a deployment with debugging on writes it.

    Routes every logger (Django's request records included) through the
    production formatter into a buffer, with debug details enabled.
    """
    import io
    import logging

    from parishkit.stewardship.observability import (
        DEBUG_LOGGING_VARIABLE,
        SafeJsonFormatter,
    )

    monkeypatch.setenv(DEBUG_LOGGING_VARIABLE, "1")
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(SafeJsonFormatter())
    root = logging.getLogger()
    # caplog.set_level goes through setLevel (clearing the logging module's
    # level cache) and restores each level afterwards.
    caplog.set_level(logging.DEBUG)
    for name in ("django", "django.request", "parishkit.stewardship.debug"):
        logger = logging.getLogger(name)
        monkeypatch.setattr(logger, "propagate", True)
        monkeypatch.setattr(logger, "disabled", False)
        caplog.set_level(logging.DEBUG, logger=name)
    root.addHandler(handler)
    yield buffer
    root.removeHandler(handler)


def _closed(monkeypatch):
    """Close the Family portal for maintenance."""
    from parishkit.stewardship.accounts import family_maintenance

    monkeypatch.setattr(
        family_maintenance,
        "current_state",
        lambda **_: family_maintenance.MaintenanceState(closed=True),
    )


def _limiter_down(monkeypatch, service):
    """Fail the limiter's first admission check, as a Valkey outage would."""
    from parishkit.stewardship.accounts.limiting import LimiterUnavailable

    def unavailable(*args, **kwargs):
        """Simulate the limiter store failing."""
        raise LimiterUnavailable("synthetic outage")

    monkeypatch.setattr(service.service.limiter, "bucket", unavailable)


def _throttled(monkeypatch, service):
    """Report the client as over its link-attempt budget."""
    monkeypatch.setattr(service.service.limiter, "bucket", lambda *args: 30)


def _lookup_fails(error):
    """Make the link lookup raise ``error`` with the token in its message."""

    def install(monkeypatch, service):
        """Replace the lookup the access view calls."""
        from parishkit.stewardship.accounts import family_authentication

        def fail(*args, token=None, **kwargs):
            """Embed the raw token, as careless exception text might."""
            raise error(f"no credential for {token}")

        monkeypatch.setattr(family_authentication, "lookup", fail)

    return install


def _revoked(monkeypatch, service):
    """Invalidate the rehearsal so the valid-looking link is refused."""
    invalidate_rehearsal(campaign_id=service.campaign.pk, admit=lambda *args: True)


@pytest.mark.parametrize(
    ("arrange", "status"),
    [
        (lambda monkeypatch, service: _closed(monkeypatch), 503),
        (_limiter_down, 503),
        (_lookup_fails(ConfigError), 503),
        (_throttled, 429),
        (_revoked, 403),
        (_lookup_fails(RuntimeError), 500),
    ],
    ids=[
        "maintenance",
        "limiter-outage",
        "config-error",
        "throttled",
        "revoked",
        "unexpected-error",
    ],
)
def test_debug_logging_never_writes_the_personal_link_token(
    family_service, monkeypatch, debug_log, arrange, status
):
    """#311: a failed personal link logs its route, never its working token."""
    arrange(monkeypatch, family_service)
    client = Client(raise_request_exception=False)
    response = client.get("/access/" + family_service.token)
    assert response.status_code == status
    output = debug_log.getvalue()
    # Django's request record proves the failing request was logged at all.
    assert "/access/[redacted]" in output
    assert family_service.token not in output
