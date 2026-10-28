"""Campaign/mode/epoch-scoped Family exchange and PostgreSQL session admission."""

from contextlib import nullcontext, suppress
from dataclasses import dataclass
from datetime import timedelta
from importlib import import_module

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import F, Q
from django.http import HttpResponseRedirect, JsonResponse, RawPostDataException
from django.middleware.csrf import rotate_token
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
    require_safe,
)

from parishkit.config import ConfigError
from parishkit.stewardship.audit.models import AuditEvent
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
from parishkit.stewardship.campaigns.engagement import (
    engagement_mode,
    record_engagement_best_effort,
)
from parishkit.stewardship.campaigns.lifecycle import (
    portal_admitted,
    staff_entry_admitted,
)
from parishkit.stewardship.campaigns.runtime import _now, campaign_facts
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.web.security import login_denial

from . import family_maintenance
from .auth_incidents import record_link_rejection, record_login_rejection
from .configuration_installation import coherent_configuration
from .cryptography import (
    CodeMacKeyring,
    CryptographicError,
    GeneralKeyring,
    TokenPublicKeyring,
    canonical_code,
    token_digest,
)
from .limiting import Counter, Limiter, LimiterUnavailable, source_network
from .policy import Principal
from .sessions import FAMILY_ABSOLUTE, FAMILY_IDLE, database_now, revoke_family_sessions


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


def _optional_public_help(slot):
    """Optional public instructions never replace the fixed entry/error fallback."""
    from parishkit.stewardship.responses.availability import public_help

    content = ""
    service = getattr(settings, "STEWARDSHIP_FAMILY_RUNTIME", None)
    if isinstance(service, FamilyRuntime):
        # Optional contact help must not turn an outage into a secondary error.
        # Its inputs are campaign-wide, independent of the rejected credential.
        # Presence may already have revoked a session in an outer transaction.
        # Roll back a failed optional SQL lookup before suppressing its error.
        with suppress(ConfigError, DatabaseError, ValueError), transaction.atomic():
            content = public_help(service, slot)
    return content


def _login_title():
    """The sign-in page's title follows the campaign modules; a failed optional
    read falls back to the neutral title rather than an error page."""
    from parishkit.stewardship.responses.availability import family_login_title

    title = _("Family login")
    service = getattr(settings, "STEWARDSHIP_FAMILY_RUNTIME", None)
    if isinstance(service, FamilyRuntime):
        with suppress(ConfigError, DatabaseError, ValueError), transaction.atomic():
            title = family_login_title(service)
    return title


def _portal_title():
    """The Family form's title is the campaign name; a failed optional read
    falls back to the neutral title rather than an error page."""
    from parishkit.stewardship.responses.availability import family_portal_title

    title = _("Stewardship")
    service = getattr(settings, "STEWARDSHIP_FAMILY_RUNTIME", None)
    if isinstance(service, FamilyRuntime):
        with suppress(ConfigError, DatabaseError, ValueError), transaction.atomic():
            title = family_portal_title(service)
    return title


def denied(kind="", *, status=403, retry=None):
    """Unknown, inactive and non-parishioner credentials use identical responses.

    ``kind`` names only the denial category ("code" or "link"), never the
    reason, so every rejected credential of one category renders identically.
    Rate limiting (429) and outages (503) always use the generic temporary
    unavailability text instead: the status already distinguishes them, and a
    Family that is merely throttled must not be told its code is wrong.
    """
    if status in {429, 503}:
        kind = "unavailable"
    response = login_denial(
        status=status,
        public_content=_optional_public_help("access_denied"),
        kind=kind,
    )
    response.stewardship_safe_error = True
    if retry:
        response["Retry-After"] = str(min(3600, max(1, int(retry))))
    return response


def _availability(request, page, *, status=200):
    """Render the campaign-wide availability page that ``/`` shows.

    The page carries only campaign-wide text, so the same bytes go to every
    visitor in the same campaign state.
    """
    return render(
        request,
        "stewardship/family-unavailable.html",
        {
            "availability_message": page.message,
            "availability_content": page.content,
            "availability_ended": page.ended,
        },
        status=status,
    )


def _ended_page(service):
    """The after-close page when the current campaign has ended, else None.

    A rejected link or code shows this instead of the generic denial (#1793),
    because invitation and reminder emails keep reaching Families after close.
    It is decided from campaign state alone, never the submitted credential,
    so it cannot reveal whether a link or code was valid. An optional read
    that fails falls back to the generic denial rather than an error page.
    """
    from parishkit.stewardship.responses.availability import unavailable_page

    page = None
    with suppress(ConfigError, DatabaseError, ValueError), transaction.atomic():
        page = unavailable_page(service)
    return page if page is not None and page.ended else None


def _rejected(request, service, kind):
    """The 403 for a rejected credential: the ended page or the generic denial.

    Only the body differs from ``denied(kind)``; the status, ``no-store`` and
    the caller's failure accounting stay the same, so rate limiting and audit
    behave exactly as for any other rejected credential.
    """
    page = _ended_page(service)
    if page is None:
        return denied(kind)
    response = _availability(request, page, status=403)
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    return response


def _scope(service, *, assisted=False):
    """Fresh coherent runtime and date gates precede every Family object lookup.

    ``assisted`` is for Open form's sign-in and the Family session it made
    (``FamilySession.assisted_by_id``, set only by SQL): that admission also
    holds after the campaign closes (``staff_entry_admitted``, #2294). Every
    Family's own code, link and session uses the portal's admission.
    """
    configuration = coherent_configuration(service.store)
    campaign = configuration.current_campaign
    admitted = staff_entry_admitted if assisted else portal_admitted
    facts = None if campaign is None else campaign_facts(campaign, configuration)
    if facts is None or not admitted(facts, _now()):
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


def lookup(service, *, code=None, token=None, lock=False, current=None, assisted=False):
    """Indexed lookup only, with no decryption scans or cross-mode fallback.

    ``assisted`` takes Open form's admission (``_scope``); only the hand-off
    passes it, and only with the Family's code.
    """
    with transaction.atomic():
        current = _scope(service, assisted=assisted) if current is None else current
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
                    # Each (campaign, key, digest) identifies at most one row.
                    # Materialize that key-bounded set before checking current
                    # eligibility to prevent a population-wide join plan. CI
                    # isolated slow execution here; its exact cause is unknown.
                    candidates = list(
                        FamilyCodeFingerprint.objects.filter(query, campaign=campaign)
                        .values_list("family_id", flat=True)
                        .distinct()
                    )
                    matches = FamilyCampaign.objects.filter(
                        pk__in=candidates, campaign=campaign, portal_eligible=True
                    ).values_list("pk", flat=True)
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


class _SessionRefused(Exception):
    """SQL refused to mint the session; roll back the new Django session."""


def _mint_session(
    session_key, family_id, *, digests=None, token=None, assisted_session=None
):
    """Create the Family session row in SQL, which re-proves the credential.

    Web has no INSERT on the Family session table (#306 M3); the SECURITY
    DEFINER login function checks the code MACs or the link token and the
    admission rules itself, and returns the new row's id or None. The call
    uses a server-side-bound psycopg cursor, so a personal link token travels
    only as a bound parameter and never appears in statement text that
    PostgreSQL might log with an error.

    ``assisted_session`` is the Admin portal session whose Open form
    hand-off is being redeemed (#2294). It selects the separate assisted
    login function, which takes only a code, checks that this Admin session
    is live and its user may open forms, admits a closed Production
    campaign too, and records the Admin on the new row
    (``assisted_by_id``). A Family's own code or link never reaches it.
    """
    from psycopg import Cursor
    from psycopg.types.json import Jsonb

    from parishkit.stewardship.observability import current_correlation

    connection.ensure_connection()
    with connection.wrap_database_errors, Cursor(connection.connection) as cursor:
        if assisted_session is not None:
            if digests is None or token is not None:
                raise ValueError("Open form signs in with the Family's code only.")
            cursor.execute(
                "SELECT public.stewardship_family_assisted_login_v1("
                "%s::varchar, %s::uuid, %s::uuid, %s::jsonb, %s::uuid)",
                [
                    session_key,
                    family_id,
                    current_correlation(),
                    Jsonb(digests),
                    assisted_session,
                ],
            )
            return cursor.fetchone()[0]
        cursor.execute(
            "SELECT public.stewardship_family_login_v1("
            "%s::varchar, %s::uuid, %s::uuid, %s::jsonb, %s::text)",
            [
                session_key,
                family_id,
                current_correlation(),
                None if digests is None else Jsonb(digests),
                token,
            ],
        )
        return cursor.fetchone()[0]


def issue_family(
    request,
    service,
    identity,
    *,
    code=None,
    token=None,
    assisted_by=None,
    assisted_session=None,
):
    """Mint a token-free, mode-bound session after checking current admission.

    Python keeps the lookup, rate limiting, prior-session revocation, audit
    and CSRF rotation; SQL creates the session row only after proving the
    same credential again (``_mint_session``). A SQL refusal rolls this whole
    sign-in back and restores the browser's previous session, so it ends in
    the same uniform denial as any other rejected credential. ``assisted_by``
    is the Admin whose Open form hand-off started this sign-in (#529), and
    ``assisted_session`` that Admin's portal session; the two come together
    and only from the hand-off, whose sign-in also admits a closed campaign
    (#2294).
    """
    if (assisted_by is None) != (assisted_session is None):
        raise ValueError("Open form names both its Admin and their session.")
    previous = request.session
    try:
        return _issue_family(
            request,
            service,
            identity,
            code=code,
            token=token,
            assisted_by=assisted_by,
            assisted_session=assisted_session,
        )
    except _SessionRefused:
        request.session = previous
        return False


def _issue_family(
    request,
    service,
    identity,
    *,
    code=None,
    token=None,
    assisted_by=None,
    assisted_session=None,
):
    """The transaction behind ``issue_family``; see there."""
    digests = None
    assisted = assisted_session is not None
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
            current = _scope(service, assisted=assisted)
            if current is None:
                return False
            if (
                lookup(service, code=code, token=token, lock=True, current=current)
                != identity
            ):
                return False
            if code is not None:
                # The same per-key MACs the lookup matched, for SQL to re-match.
                digests = service.mac.lookups(identity[1], code)
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
        prior = list(
            FamilySession.objects.select_for_update().filter(
                session_id=request.session.session_key, revoked_at__isnull=True
            )
        )
        revoke_family_sessions(prior, now=now)
        request.session = import_module(settings.SESSION_ENGINE).SessionStore()
        request.session["family"] = str(family_id)
        request.session.set_expiry(now + FAMILY_ABSOLUTE)
        request.session.save()
        row_id = _mint_session(
            request.session.session_key,
            family_id,
            digests=digests,
            token=token,
            assisted_session=assisted_session,
        )
        if row_id is None:
            raise _SessionRefused
        AuditEvent.objects.create(
            event_type="family_login", subject_id=row_id, actor_id=family_id
        )
        if assisted_by is not None:
            from .assisted_entry import mark

            mark(request, row_id, assisted_by)
        # "Link followed" for the response funnel (#477): durable, unlike the
        # session row, which cleanup removes an hour after the last activity.
        # An Open form sign-in (#529) is Staff, not the Family following its
        # link, so it records no engagement here; the form it then opens is
        # still recorded as opened, as any form issuance is.
        if assisted_by is None:
            record_engagement_best_effort(
                family_id=family_id,
                mode=engagement_mode(mode),
                rehearsal_epoch_id=epoch if mode == "testing" else None,
                actor_id=family_id,
                seen_at=now,
                link_at=now,
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
        # Open form's session keeps Open form's admission after close
        # (#2294); SQL alone sets assisted_by_id, never a Family's sign-in.
        now = database_now()
        current = _scope(service, assisted=row.assisted_by_id is not None)
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
            # While an Administrator has closed the portal for maintenance,
            # keepalive (or a Family request already in flight when it closed)
            # can still reach here; leave the Family's campaign row alone so it
            # does not contend with the Administrator's data fix.
            if not family_maintenance.current_state().closed:
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
    from parishkit.stewardship.responses.availability import unavailable_page

    try:
        service = runtime()
        if request.method != "POST":
            page = unavailable_page(service)
            if page:
                return _availability(request, page)
            return render(
                request,
                "stewardship/family-login.html",
                {
                    "login_help": _optional_public_help("login_help"),
                    "login_title": _login_title(),
                },
            )
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
                    "family_pair",
                    source_network(request.client_address) + ":" + fingerprint,
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
        record_login_rejection("family_login_failed")
        if delay:
            return denied(status=429, retry=delay)
        return _rejected(request, service, "code")
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
        # Valid opaque links remain usable during a limiter outage. Failed links
        # still contribute when the store is available, without permanent IDs.
        with suppress(LimiterUnavailable):
            service.limiter.failed(
                "family",
                request.client_address,
                candidate=service.limiter.fingerprint("invalid_link", token),
            )
        record_link_rejection()
        return _rejected(request, service, "link")
    except (LimiterUnavailable, CryptographicError, ConfigError):
        return denied(status=503, retry=5)


@require_safe
def portal(request):
    """Render an answer-free shell; the page script loads the private form."""
    try:
        principal = authenticated_family(request, service=runtime(), activity=True)
        if principal is None:
            return HttpResponseRedirect("/")
        return render(
            request,
            "stewardship/family.html",
            {
                "testing": request.family_session.mode == "testing",
                "portal_title": _portal_title(),
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
    try:
        nonempty = bool(request.body)
    except RawPostDataException:
        # CSRF processing can already have consumed a multipart stream.
        # Such a request cannot be the required empty activity claim.
        nonempty = True
    if nonempty:
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
    from parishkit.stewardship.responses.baselines import cancel_session_baselines

    with work_transaction():
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
        if row is not None:
            cancel_session_baselines([row.pk])
    request.session = import_module(settings.SESSION_ENGINE).SessionStore()
    return HttpResponseRedirect("/")
