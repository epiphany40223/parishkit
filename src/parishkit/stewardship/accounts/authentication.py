"""Google-only provider boundary with early admission and fresh signed identity.

Allauth owns PKCE, authorization URL construction, state and provider exchange.
This application deliberately exposes none of its signup/password/connect/token
endpoints and never persists SocialAccount, SocialToken or Django User objects.
"""

import secrets
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter, get_adapter
from allauth.socialaccount.providers.google.views import (
    GoogleOAuth2Adapter,
    _verify_and_decode,
)
from allauth.socialaccount.providers.oauth2.client import OAuth2Error
from allauth.socialaccount.providers.oauth2.views import OAuth2CallbackView
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import HttpResponseRedirect
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
from parishkit.stewardship.web.security import login_denial

from .limiting import Counter, Limiter, LimiterUnavailable
from .models import OAuthStateConsumption, PolicyEpoch, PortalUser, SystemConfiguration
from .policy import current_principal
from .policy_schema import normalized_domain, normalized_email
from .sessions import (
    ADMIN_IDLE,
    authenticated_admin,
    database_now,
    end_admin,
    issue_admin,
    revocation_epoch,
)


@dataclass(frozen=True)
class AuthRuntime:
    """Validated authority and limiter, assembled by the service startup owner."""

    store: object
    limiter: Limiter
    setup_complete: Callable[[], bool] | None = None

    def configured(self):
        """Read the owning setup marker every request; absence always fails closed.

        ADM-02 supplies its durable completed-setup marker after its atomic
        finalization. A prepared YAML snapshot alone is not completed setup.
        """
        if self.setup_complete is None:
            return False
        result = self.setup_complete()
        if type(result) is not bool:
            raise ConfigError("The setup completion marker is unavailable.")
        return result


def runtime():
    """Missing startup integration is unavailable, not a development auth bypass."""
    value = settings.STEWARDSHIP_AUTH_RUNTIME
    if not isinstance(value, AuthRuntime):
        raise LimiterUnavailable("Authentication is temporarily unavailable.")
    return value


def denial(*, status=403, retry=None, admin=True):
    """Uniform retryable response, with no provider details or denied identity."""
    response = login_denial(admin=admin, status=status)
    response.stewardship_safe_error = True
    if retry:
        response["Retry-After"] = str(min(3600, max(1, int(retry))))
    return response


def ip_counter(limiter, source, *, initiation=False):
    """Callbacks count failures only; initiation is counted before state allocation."""
    return Counter(
        "admin_start" if initiation else "admin_callback",
        limiter.fingerprint("ip", source),
        limiter.limits.admin_starts if initiation else limiter.limits.admin_callbacks,
        600,
    )


def record_failure(request, *, identity="", counter=None):
    """Even early rejected callbacks contribute once; never parse tokens here."""
    if getattr(request, "auth_failure_counted", False):
        return 0
    request.auth_failure_counted = True
    limiter = runtime().limiter
    counters = [ip_counter(limiter, request.client_address)]
    if counter:
        counters.append(counter)
    delay = limiter.counters(counters, failure=True)
    limiter.failed("admin", request.client_address, identity=identity)
    AuditEvent.objects.create(event_type="admin_login_denied")
    return min(3600, delay * (2 if limiter.elevated("admin") else 1))


class AuthLimitMiddleware:
    """Run after trusted-client resolution, before session/state/provider access."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        """Secure opaque links alone use the bounded per-process outage fallback."""
        callback = request.path_info == "/admin/oauth/callback"
        admin = request.path_info in {
            "/admin/login",
            "/admin/oauth/start",
            "/admin/oauth/callback",
        }
        access = request.path_info.startswith("/access/")
        if not admin and not access:
            return self.get_response(request)
        try:
            limiter = runtime().limiter
            delay = limiter.bucket(
                "admin" if admin else "access", request.client_address
            )
            if not delay and callback:
                delay = limiter.counters([ip_counter(limiter, request.client_address)])
            if delay:
                if callback:
                    record_failure(request)
                elif access:
                    with suppress(LimiterUnavailable):
                        limiter.failed("family", request.client_address)
                return denial(status=429, retry=delay, admin=admin)
        except LimiterUnavailable:
            return denial(status=503, retry=5, admin=admin)
        return self.get_response(request)


class GoogleBoundary(DefaultSocialAccountAdapter):
    """No account creation/connect flow may cross our PortalUser/session boundary."""

    def is_open_for_signup(self, request, sociallogin):
        return False

    def pre_social_login(self, request, sociallogin):
        raise ImmediateHttpResponse(denial())

    def on_authentication_error(self, request, provider, **kwargs):
        """Discard provider exceptions, raw callback parameters and identity hints."""
        try:
            delay = record_failure(request)
            response = denial(status=429 if delay else 403, retry=delay)
        except LimiterUnavailable:
            response = denial(status=503, retry=5)
        raise ImmediateHttpResponse(response)


class SignedGoogleAdapter(GoogleOAuth2Adapter):
    """Require signed OIDC claims even after a direct TLS-protected token exchange."""

    def complete_login(self, request, app, token, **kwargs):
        """A userinfo-only response cannot establish signed hosted-domain evidence."""
        credential = kwargs["response"].get("id_token")
        if type(credential) is not str or not 0 < len(credential) <= 16384:
            raise OAuth2Error("Verified identity is unavailable.")
        try:
            if jwt.get_unverified_header(credential).get("alg") != "RS256":
                raise OAuth2Error("Verified identity is unavailable.")
            claims = _verify_and_decode(app, credential, verify_signature=True)
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            raise OAuth2Error("Verified identity is unavailable.") from None
        state = request.stewardship_oauth_state
        nonce = claims.get("nonce")
        if (
            type(nonce) is not str
            or len(nonce) > 128
            or not nonce.isascii()
            or not secrets.compare_digest(nonce, state["data"]["nonce"])
            or claims.get("email_verified") is not True
            or type(claims.get("sub")) is not str
            or not 0 < len(claims["sub"]) <= 255
            or type(claims.get("exp")) is not int
            or type(claims.get("iat")) is not int
        ):
            raise OAuth2Error("Verified identity is unavailable.")
        try:
            email = normalized_email(claims.get("email"))
            hosted = normalized_domain(claims["hd"]) if "hd" in claims else None
        except ConfigError:
            raise OAuth2Error("Verified identity is unavailable.") from None
        authenticated_at = verified_authentication_time(claims, state)
        raise ImmediateHttpResponse(
            complete_identity(
                request, claims["sub"], email, hosted, authenticated_at=authenticated_at
            )
        )


def verified_authentication_time(claims, state):
    """Preserve signed provider authentication independently of local login time.

    Token issuance and account selection alone do not prove reauthentication.
    An existing Google session may establish ordinary identity without granting
    the five-minute privileged window. The one-use nonce binds this exchange;
    auth_time records the provider's authentication, which may predate it.
    """
    now = database_now()
    authenticated = claims.get("auth_time")
    initiated = state.get("data", {}).get("initiated_at")
    if (
        type(authenticated) is not int
        or type(initiated) is not int
        or not now.timestamp() - 900 <= initiated <= now.timestamp()
        or not 0 < authenticated <= now.timestamp() + 30
        or authenticated > claims["iat"] + 30
    ):
        raise OAuth2Error("Verified identity is unavailable.")
    return min(now, datetime.fromtimestamp(authenticated, UTC))


class GoogleCallback(OAuth2CallbackView):
    """State is one-use and recovery-bound before any external provider request."""

    def _get_state(self, request, provider):
        state, response = super()._get_state(request, provider)
        if response:
            return state, response
        data = state.get("data", {})
        if data.get("recovery_epoch") != revocation_epoch() or not data.get("nonce"):
            delay = record_failure(request)
            return None, denial(status=429 if delay else 403, retry=delay)
        request.stewardship_oauth_state = state
        fingerprint = runtime().limiter.fingerprint(
            "oauth_state", request.session.session_key + "\x00" + data["nonce"]
        )
        _, created = OAuthStateConsumption.objects.get_or_create(
            fingerprint=fingerprint,
            defaults={"expires_at": database_now() + timedelta(minutes=15)},
        )
        if not created:
            delay = record_failure(request)
            return None, denial(status=429 if delay else 403, retry=delay)
        # Persist the one-use receipt before provider I/O, independently of later
        # session saves by simultaneous browser tabs or callback requests.
        request.session.save()
        return state, response


def complete_identity(request, subject, email, hosted, *, authenticated_at):
    """Only a verified Google identity can be created or refresh its email claims."""
    service = runtime()
    epoch = (
        PolicyEpoch.objects.order_by("-sequence")
        .values_list("sequence", flat=True)
        .first()
    )
    if epoch is None:
        raise ConfigError("Authentication policy is unavailable.")
    fingerprint = service.limiter.fingerprint("identity", subject + "\x00" + email)
    counter = Counter(
        f"admin_identity_{epoch}",
        fingerprint,
        service.limiter.limits.admin_identity,
        900,
    )
    delay = service.limiter.counters([counter])
    if delay:
        record_failure(request, identity=fingerprint, counter=counter)
        return denial(status=429, retry=delay)
    with transaction.atomic():
        if (
            request.stewardship_oauth_state["data"]["recovery_epoch"]
            != revocation_epoch()
        ):
            return denial()
        user, created = PortalUser.objects.select_for_update().get_or_create(
            google_subject=subject,
            defaults=dict(
                email=email, hosted_domain=hosted, verified_at=database_now()
            ),
        )
        if not created:
            PortalUser.objects.filter(pk=user.pk).update(
                email=email,
                hosted_domain=hosted,
                verified_at=database_now(),
                version=F("version") + 1,
            )
        principal = None if user.disabled else current_principal(service.store, user.pk)
        if principal is not None and principal.roles:
            issue_admin(
                request, user.pk, store=service.store, authenticated_at=authenticated_at
            )
            rotate_token(request)
    if principal is None or not principal.roles:
        delay = record_failure(request, identity=fingerprint, counter=counter)
        return denial(status=429 if delay else 403, retry=delay)
    service.limiter.clear(counter)
    return HttpResponseRedirect("/admin/")


@require_http_methods(["GET", "HEAD", "POST"])
def login(request):
    """Only a CSRF-protected POST initiates authentication; ignore dynamic scopes."""
    try:
        service = runtime()
        if request.method != "POST":
            return render(request, "stewardship/login.html")
        limiter = service.limiter
        counter = ip_counter(limiter, request.client_address, initiation=True)
        delay = limiter.counters([counter])
        if delay:
            return denial(status=429, retry=delay)
        limiter.counters([counter], failure=True)
        nonce = secrets.token_urlsafe(32)
        provider = get_adapter(request).get_provider(request, "google")
        return provider.redirect(
            request,
            process="login",
            next_url="/admin/",
            data={
                "nonce": nonce,
                "recovery_epoch": revocation_epoch(),
                "initiated_at": int(database_now().timestamp()),
            },
            scope=["openid", "email"],
            auth_params={
                "nonce": nonce,
                "prompt": "select_account",
                "max_age": "0",
                "claims": '{"id_token":{"auth_time":{"essential":true}}}',
            },
        )
    except (LimiterUnavailable, ConfigError):
        return denial(status=503, retry=5)


@require_GET
def callback(request):
    """All errors use the same safe retry page; the library owns exchange failures."""
    try:
        return GoogleCallback.adapter_view(SignedGoogleAdapter)(request)
    except (LimiterUnavailable, ConfigError):
        return denial(status=503, retry=5)


@require_POST
def logout(request):
    """CSRF-protected logout revokes authority and drops only the Admin cookie."""
    end_admin(request)
    return HttpResponseRedirect("/admin/login")


@require_safe
def index(request):
    """Role-bearing shell; feature pages remain with their owning work packages."""
    try:
        principal = authenticated_admin(request, store=runtime().store, activity=True)
    except (LimiterUnavailable, ConfigError):
        return denial(status=503, retry=5)
    if principal is None:
        return HttpResponseRedirect("/admin/login")
    config = SystemConfiguration.objects.get()
    if config.restore_review_required:
        return denial(status=503, retry=5)
    return render(
        request,
        "stewardship/home.html",
        {
            "principal": principal,
            "configuration": config,
            "server_now": database_now(),
            "absolute_deadline": request.portal_session.expires_at,
            "deadline": min(
                request.portal_session.expires_at,
                request.portal_session.last_activity_at + ADMIN_IDLE,
            ),
        },
    )
