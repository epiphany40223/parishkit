"""Campaign/mode/epoch-scoped Family exchange and PostgreSQL session admission."""

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import timedelta
from importlib import import_module

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F, Q
from django.http import HttpResponseRedirect, JsonResponse
from django.middleware.csrf import rotate_token
from django.shortcuts import render
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
    require_safe,
)

from parishkit.config import ConfigError
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    DeploymentCredentialState,
    FamilyAccessToken,
    FamilyCampaign,
    FamilyCodeFingerprint,
    FamilySession,
    RehearsalCodeFingerprint,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.lifecycle import portal_admitted
from parishkit.stewardship.campaigns.runtime import _now, campaign_facts
from parishkit.stewardship.web.security import login_denial

from .configuration_installation import coherent_configuration
from .cryptography import (
    CodeMacKeyring,
    CryptographicError,
    GeneralKeyring,
    TokenPublicKeyring,
    canonical_code,
    token_digest,
)
from .limiting import Counter, Limiter, LimiterUnavailable
from .policy import Principal
from .sessions import FAMILY_ABSOLUTE, FAMILY_IDLE, database_now


@dataclass(frozen=True)
class FamilyRuntime:
    """The Family web process has public token keys, never a private token ring."""

    store: object
    limiter: Limiter
    general: GeneralKeyring
    mac: CodeMacKeyring
    public: TokenPublicKeyring

    def __post_init__(self):
        if (
            not isinstance(self.general, GeneralKeyring)
            or not isinstance(self.mac, CodeMacKeyring)
            or not isinstance(self.public, TokenPublicKeyring)
        ):
            raise TypeError("Family web cryptographic service boundaries are invalid.")


def runtime():
    """Production startup owns the configured service bundle; no bypass exists."""
    service = settings.STEWARDSHIP_FAMILY_RUNTIME
    if not isinstance(service, FamilyRuntime):
        raise LimiterUnavailable("Family access is temporarily unavailable.")
    return service


def denied(*, status=403, retry=None):
    """Unknown, inactive and non-parishioner credentials use identical responses."""
    response = login_denial(status=status)
    response.stewardship_safe_error = True
    if retry:
        response["Retry-After"] = str(min(3600, max(1, int(retry))))
    return response


def _scope(service):
    """Fresh coherent runtime and date gates precede every Family object lookup."""
    configuration = coherent_configuration(service.store)
    campaign = configuration.current_campaign
    if campaign is None or not portal_admitted(
        campaign_facts(campaign, configuration), _now()
    ):
        return None
    scope = CampaignCredentialState.objects.filter(
        campaign=campaign, go_live_gate=False, population_dirty=False
    ).first()
    if scope is None:
        return None
    deployment = DeploymentCredentialState.objects.get()
    if configuration.mode == "testing" and (
        scope.rehearsal_epoch_id is None or scope.rehearsal_epoch.state != "active"
    ):
        return None
    return configuration, campaign, scope, deployment


def lookup(service, *, code=None, token=None, lock=False):
    """Indexed lookup only, with no decryption scans or cross-mode fallback."""
    with transaction.atomic():
        current = _scope(service)
        if current is None:
            return None
        configuration, campaign, scope, deployment = current
        testing = configuration.mode == "testing"
        if code is not None:
            with key_set_lock(service.mac):
                query = Q(pk__in=[])
                for key, digest in service.mac.lookups(campaign.pk, code).items():
                    query |= Q(key_id=key, digest=digest)
                if testing:
                    matches = (
                        RehearsalCodeFingerprint.objects.filter(
                            query,
                            epoch_id=scope.rehearsal_epoch_id,
                            credential__family__portal_eligible=True,
                        )
                        .values_list("credential__family_id", flat=True)
                        .distinct()
                    )
                else:
                    matches = (
                        FamilyCodeFingerprint.objects.filter(
                            query, campaign=campaign, family__portal_eligible=True
                        )
                        .values_list("family_id", flat=True)
                        .distinct()
                    )
                found = list(matches[:2])
                found = found[0] if len(found) == 1 else None
        else:
            try:
                digest = token_digest(token, campaign.pk)
            except CryptographicError:
                return None
            if token.startswith("test.") != testing:
                return None
            if testing:
                query = RehearsalCredential.objects.filter(
                    epoch_id=scope.rehearsal_epoch_id,
                    token_digest=digest,
                    family__portal_eligible=True,
                )
            else:
                query = FamilyAccessToken.objects.filter(
                    campaign=campaign,
                    generation_id=campaign.active_token_generation_id,
                    generation__credential_epoch=deployment.family_link_epoch,
                    generation__state="active",
                    family__portal_eligible=True,
                    digest=digest,
                    destroyed_at__isnull=True,
                )
            if lock:
                query = query.select_for_update(of=("self",))
            found = query.values_list("family_id", flat=True).first()
        if found is None:
            return None
        return (
            found,
            campaign.pk,
            configuration.mode,
            scope.rehearsal_epoch_id if testing else None,
            deployment.family_link_epoch,
        )


def issue_family(request, service, identity, *, code=None, token=None):
    """Mint a token-free, mode-bound session after checking current admission."""
    with transaction.atomic(), connection.cursor() as cursor:
        # Shared locks allow unrelated logins concurrently while keeping source,
        # restore and mode writers from changing admission during session minting.
        cursor.execute("SELECT id FROM stewardship_system_configuration FOR SHARE")
        cursor.execute("SELECT id FROM stewardship_credential_deployment FOR SHARE")
        with key_set_lock(service.mac) if code is not None else nullcontext():
            cursor.execute(
                "SELECT id FROM stewardship_campaign_credentials "
                "WHERE campaign_id=%s FOR SHARE",
                [identity[1]],
            )
            if lookup(service, code=code, token=token, lock=True) != identity:
                return False
        current = _scope(service)
        if current is None:
            return False
        configuration, campaign, scope, deployment = current
        family_id, campaign_id, mode, epoch, credential_epoch = identity
        if (
            campaign.pk != campaign_id
            or configuration.mode != mode
            or credential_epoch != deployment.family_link_epoch
            or (mode == "testing" and scope.rehearsal_epoch_id != epoch)
            or not FamilyCampaign.objects.filter(
                pk=family_id, campaign=campaign, portal_eligible=True
            ).exists()
        ):
            return False
        now = database_now()
        FamilySession.objects.filter(
            session_id=request.session.session_key, revoked_at__isnull=True
        ).update(revoked_at=now, version=F("version") + 1)
        request.session = import_module(settings.SESSION_ENGINE).SessionStore()
        request.session["family"] = str(family_id)
        request.session.set_expiry(now + FAMILY_ABSOLUTE)
        request.session.save()
        row = FamilySession.objects.create(
            session_id=request.session.session_key,
            family_id=family_id,
            mode=mode,
            rehearsal_epoch_id=epoch,
            credential_epoch=credential_epoch,
            authenticated_at=now,
            last_activity_at=now,
            expires_at=now + FAMILY_ABSOLUTE,
        )
        AuditEvent.objects.create(
            event_type="family_login", subject_id=row.pk, actor_id=family_id
        )
        rotate_token(request)
        return True


def authenticated_family(
    request, *, service, activity=False, keepalive=False, read_only=False
):
    """Neither passive polling nor client claims can extend the absolute lifetime."""
    if read_only and (activity or keepalive):
        raise ValueError("Read-only Family admission cannot renew activity.")
    with transaction.atomic():
        query = (
            FamilySession.objects.all()
            if read_only
            else FamilySession.objects.select_for_update()
        )
        row = query.filter(session_id=request.session.session_key).first()
        if row is None or row.revoked_at is not None:
            return None
        now, current = database_now(), _scope(service)
        valid = (
            now < min(row.expires_at, row.last_activity_at + FAMILY_IDLE)
            and current is not None
        )
        if valid:
            configuration, campaign, scope, deployment = current
            valid = (
                row.mode == configuration.mode
                and row.credential_epoch == deployment.family_link_epoch
                and (
                    row.mode != "testing"
                    or row.rehearsal_epoch_id == scope.rehearsal_epoch_id
                )
                and FamilyCampaign.objects.filter(
                    pk=row.family_id, campaign=campaign, portal_eligible=True
                ).exists()
            )
        if not valid:
            if not read_only:
                FamilySession.objects.filter(pk=row.pk).update(
                    revoked_at=max(now, row.last_activity_at), version=F("version") + 1
                )
                AuditEvent.objects.create(
                    event_type="family_session_ended",
                    subject_id=row.pk,
                    actor_id=row.family_id,
                )
            return None
        if (
            keepalive
            and row.last_keepalive_at is not None
            and now < row.last_keepalive_at + timedelta(minutes=5)
        ):
            keepalive = False
        if activity or keepalive:
            row.last_activity_at = now
            if keepalive:
                row.last_keepalive_at = now
            row.version += 1
            row.save()
            FamilyCampaign.objects.filter(pk=row.family_id).update(
                last_activity_at=now,
                version=F("version") + 1,
            )
        request.family_session = row
        request.principal = Principal(row.family_id, family_id=row.family_id)
        return request.principal


def _ip_counter(service, source):
    """Shared-network allowance tightens temporarily only during detected abuse."""
    base = service.limiter.limits.family_ip
    limit = max(1, base // 2) if service.limiter.elevated("family") else base
    return Counter("family_ip", service.limiter.fingerprint("ip", source), limit, 600)


@require_http_methods(["GET", "HEAD", "POST"])
def entry(request):
    """Eight-letter manual entry; code values never enter a URL, log or audit."""
    try:
        service = runtime()
        if request.method != "POST":
            return render(request, "stewardship/family-login.html")
        ip = _ip_counter(service, request.client_address)
        delay = service.limiter.counters([ip])
        if delay:
            service.limiter.failed("family", request.client_address)
            return denied(status=429, retry=delay)
        code = canonical_code(request.POST.get("code"))
        pair, fingerprint = None, ""
        if code is not None:
            fingerprint = service.limiter.fingerprint("family_candidate", code)
            pair = Counter(
                "family_pair",
                service.limiter.fingerprint(
                    "family_pair", str(request.client_address) + ":" + fingerprint
                ),
                service.limiter.limits.family_pair,
                900,
            )
            identity = lookup(service, code=code)
            # Pair failures do not lock a now-valid/reactivated code. Only the
            # independent source-IP budget can deny a successful Family login.
            if identity is not None and issue_family(
                request, service, identity, code=code
            ):
                service.limiter.clear(pair)
                return HttpResponseRedirect("/family/")
        delay = service.limiter.counters([ip, pair] if pair else [ip], failure=True)
        service.limiter.failed("family", request.client_address, candidate=fingerprint)
        AuditEvent.objects.create(event_type="family_login_failed")
        return denied(status=429 if delay else 403, retry=delay)
    except (LimiterUnavailable, CryptographicError, ConfigError):
        return denied(status=503, retry=5)


@require_GET
def access(request, token):
    """Coarse middleware has already admitted this path without hashing its token."""
    try:
        service = runtime()
        identity = lookup(service, token=token)
        if identity is not None and issue_family(
            request, service, identity, token=token
        ):
            return HttpResponseRedirect("/family/")
        with transaction.atomic():
            record_action(
                Action.INVALID_LINK,
                actor_kind=ActorKind.SYSTEM,
                context={
                    "outcome": Outcome.DENIED,
                    "source_fingerprint": service.limiter.fingerprint(
                        "ip",
                        request.client_address,
                    ),
                    "candidate_fingerprint": service.limiter.fingerprint(
                        "invalid_link",
                        token,
                    ),
                },
            )
        return denied()
    except (LimiterUnavailable, CryptographicError, ConfigError):
        return denied(status=503, retry=5)


@require_safe
def portal(request):
    """The authenticated flow shell does not expose future census/financial data."""
    try:
        principal = authenticated_family(request, service=runtime(), activity=True)
        if principal is None:
            return HttpResponseRedirect("/")
        return render(
            request,
            "stewardship/family.html",
            {
                "server_now": database_now(),
                "absolute_deadline": request.family_session.expires_at,
                "deadline": min(
                    request.family_session.expires_at,
                    request.family_session.last_activity_at + FAMILY_IDLE,
                ),
            },
        )
    except (LimiterUnavailable, CryptographicError, ConfigError):
        return denied(status=503, retry=5)


@require_POST
def keepalive(request):
    """An explicitly untrusted, empty CSRF-valid activity claim may refresh idle."""
    if request.body:
        return denied(status=400)
    try:
        principal = authenticated_family(request, service=runtime(), keepalive=True)
        if principal is None:
            return denied()
        row = request.family_session
        return JsonResponse(
            {
                "idle_deadline": min(
                    row.expires_at, row.last_activity_at + FAMILY_IDLE
                ).isoformat(),
                "absolute_deadline": row.expires_at.isoformat(),
            }
        )
    except (LimiterUnavailable, CryptographicError, ConfigError):
        return denied(status=503, retry=5)


@require_POST
def logout(request):
    """Keep the Admin cookie untouched while ending this Family's authority."""
    with transaction.atomic():
        row = (
            FamilySession.objects.select_for_update()
            .filter(session_id=request.session.session_key)
            .first()
        )
        if row is not None and row.revoked_at is None:
            FamilySession.objects.filter(pk=row.pk).update(
                revoked_at=max(database_now(), row.last_activity_at),
                version=F("version") + 1,
            )
            AuditEvent.objects.create(
                event_type="family_logout", subject_id=row.pk, actor_id=row.family_id
            )
    request.session = import_module(settings.SESSION_ENGINE).SessionStore()
    return HttpResponseRedirect("/")
