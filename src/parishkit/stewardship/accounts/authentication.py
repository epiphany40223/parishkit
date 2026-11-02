"""Google-only provider boundary with early admission and signed identity.

Allauth owns PKCE, authorization URL construction, state and provider exchange.
This application deliberately exposes none of its signup/password/connect/token
endpoints and never persists SocialAccount, SocialToken or Django User objects.
"""

import logging
import secrets
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter, get_adapter
from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers.base import AuthError
from allauth.socialaccount.providers.google.views import (
    GoogleOAuth2Adapter,
    _verify_and_decode,
)
from allauth.socialaccount.providers.oauth2.client import OAuth2Error
from allauth.socialaccount.providers.oauth2.views import OAuth2CallbackView
from django.conf import settings
from django.core.exceptions import MultipleObjectsReturned
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F
from django.http import HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token, rotate_token
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
    require_safe,
)

from parishkit.config import ConfigError
from parishkit.stewardship.audit.schemas import Action, ActorKind
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.observability import debug_swallowed
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    validation_response,
)
from parishkit.stewardship.web.error_pages import GENERIC_DENIAL
from parishkit.stewardship.web.namespaces import ADMIN_HOME, admin_return_path
from parishkit.stewardship.web.refusals import (
    UserFacingDenied,
    UserFacingGone,
    annotate,
    gone_response,
    mark_recorded,
    with_reason,
)
from parishkit.stewardship.web.security import ADDRESS_CHECK_LINES, login_denial

from . import address_matching
from .admin_session_store import SessionStore as AdminSessionStore
from .auth_incidents import record_account_refusal, record_login_rejection
from .limiting import Counter, Limiter, LimiterUnavailable
from .models import OAuthStateConsumption, PolicyEpoch, PortalUser
from .policy import current_principal
from .policy_schema import normalized_domain, normalized_email
from .sessions import (
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


def denial(
    *,
    status=403,
    retry=None,
    admin=True,
    sign_in=False,
    account=None,
    check=None,
    line=None,
):
    """Retryable response, with no provider details, naming its refusal kind.

    The kind follows the status first: an outage (503) uses the
    temporary-unavailability text; a rate limit (429) reads "Too many
    sign-in attempts" on the Admin side (every Admin 429 is a sign-in
    route's) and the temporary text on the Family side, as Family
    ``denied()`` does. Report views answer an outage with
    ``web.report_errors.report_unavailable`` instead, so Admins see a report
    error rather than this sign-in wording.

    Any other refusal of sign-in itself (``sign_in``: OAuth start, callback,
    login page) says why (#2116). ``account`` is ``(request, address)`` for
    the one case where Google verified the address this callback and policy
    gave it no Admin access: the page names that address, server-side in
    this response only, with a "Sign in with a different account" form.
    Every other sign-in refusal (cancelled at Google, a provider error, a
    used or expired OAuth state, a recovery-epoch change) reads "Sign-in
    didn't finish". Google does the authentication, so only the person who
    just proved control of an address learns whether it has access.
    Any other refusal keeps the generic "Access unavailable" text for a
    reader without a current session; a browser's page load by a signed-in
    reader is drawn by the browser-error middleware without the sign-in
    wording instead, and a signed-in reader's script gets the typed
    ``denied`` JSON with that wording (``web.error_pages.GENERIC_DENIAL``,
    #2165, #2172).

    With ``account``, ``check`` shows the Check your other email addresses
    page instead (#2343): ``{"due": match}`` is its 30-day "again" form for
    that standing match, an empty dict its first-check form. ``line`` adds
    one verified-address outcome's line to "No access with this account"
    (``declined``, ``none``, ``several``, ``own_account_only`` or
    ``taken``).

    Every report view sends a ``PermissionError`` here, including the
    ``UserFacingGone`` refusal of a campaign that is not the current one
    (navigation rule 10, raised by ``reports.read_admission``). That one is
    answered with its own 410 page instead, which explains that reports
    show the current campaign only, rather than suggesting signing in again.
    A ``UserFacingDenied`` refusal (a Ministry leader who leads no Ministry
    now, #939) likewise keeps its 403 but shows its own plain explanation.
    Reading the exception being handled keeps that in one place instead of
    an extra ``except`` clause in every report view.
    """
    if account is not None and not sign_in:
        # The address page answers only the sign-in that just verified it;
        # a signed-in refusal naming an address would be a caller's mistake.
        raise ValueError("account is only for a sign-in refusal")
    if (check is not None or line is not None) and account is None:
        raise ValueError("a verified-address page needs its verified account")
    error = sys.exception()
    if isinstance(error, UserFacingGone):
        debug_swallowed("admin request refused")
        return annotate(gone_response(error), error)
    if isinstance(error, UserFacingDenied):
        # A reviewed refusal says plainly why, still as a 403 (#939: a
        # Ministry leader who leads no Ministry now), never a sign-in prompt.
        debug_swallowed("admin request refused")
        return annotate(
            validation_response(
                [FieldError(ErrorCode.DENIED)], status=403, refusal=error.refusal
            ),
            error,
        )
    # Callers deny from inside an except block; record what actually failed.
    debug_swallowed("admin request denied")
    extra = {}
    if status == 503 or (status == 429 and not admin):
        kind = "unavailable"
    elif status == 429:
        kind = "rate_limited"
    elif account is not None:
        request, address = account
        kind = "no_access" if check is None else "address_check"
        extra = {"account": address, "csrf_token": get_token(request)}
        if check is not None:
            extra["due"] = check.get("due")
        if line is not None:
            extra["line"] = line
    elif sign_in:
        kind = "unfinished"
    else:
        kind = ""
    response = login_denial(admin=admin, status=status, kind=kind, **extra)
    response.stewardship_safe_error = True
    if admin and not kind:
        # Its sign-in wording suits only a signed-out reader (#2165).
        setattr(response, GENERIC_DENIAL, True)
    if retry:
        response["Retry-After"] = str(min(3600, max(1, int(retry))))
    # The exception being handled, if any, names the refusal in its log;
    # without one this is an access check's own refusal (#1444).
    if error is not None:
        return annotate(response, error)
    return with_reason(response, "denied")


def ip_counter(limiter, source, *, initiation=False):
    """Callbacks count failures only; initiation is counted before state allocation."""
    return Counter(
        "admin_start" if initiation else "admin_callback",
        limiter.fingerprint("ip", source),
        limiter.limits.admin_starts if initiation else limiter.limits.admin_callbacks,
        600,
    )


def record_failure(request, *, identity="", counter=None, sampled=True):
    """Even early rejected callbacks contribute once; never parse tokens here.

    ``sampled=False`` skips the anonymous ``admin_login_denied`` sample for a
    refusal its caller has already named (``record_account_refusal``); the
    limiter accounting is the same either way.
    """
    if getattr(request, "auth_failure_counted", False):
        return 0
    request.auth_failure_counted = True
    # The sign-in's own record (the admin_login_denied sample, or the
    # caller's admin_login_refused) stands for this refusal: the refusal
    # log adds none (#1403).
    mark_recorded(request)
    limiter = runtime().limiter
    counters = [ip_counter(limiter, request.client_address)]
    if counter:
        counters.append(counter)
    delay = limiter.counters(counters, failure=True)
    limiter.failed("admin", request.client_address, identity=identity)
    if sampled:
        record_login_rejection("admin_login_denied")
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
            "/admin/login/addresses/",
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

    def get_provider(self, request, provider, client_id=None):
        """Missing/ambiguous operational app configuration is retryable, not a 500."""
        try:
            return super().get_provider(request, provider, client_id=client_id)
        except (SocialApp.DoesNotExist, MultipleObjectsReturned):
            raise LimiterUnavailable(
                "Google login is temporarily unavailable."
            ) from None

    def is_open_for_signup(self, request, sociallogin):
        return False

    def pre_social_login(self, request, sociallogin):
        raise ImmediateHttpResponse(denial(sign_in=True))

    def on_authentication_error(self, request, provider, **kwargs):
        """Discard provider exceptions, raw callback parameters and identity hints.

        A cancel at Google during the second sign-in step (#2343: the
        person said no to sharing their addresses) is that check's Declined
        outcome, when its pending step is still this browser's.
        """
        state = getattr(request, "stewardship_oauth_state", None) or {}
        if (
            kwargs.get("error") == AuthError.CANCELLED
            and state.get("data", {}).get("step") == SECOND_STEP
        ):
            raise ImmediateHttpResponse(declined_at_google(request, state))
        if kwargs.get("error") == AuthError.CANCELLED:
            raise ImmediateHttpResponse(denial(sign_in=True))
        try:
            delay = record_failure(request)
            response = denial(status=429 if delay else 403, retry=delay, sign_in=True)
        except LimiterUnavailable:
            response = denial(status=503, retry=5)
        raise ImmediateHttpResponse(response)


class SignedGoogleAdapter(GoogleOAuth2Adapter):
    """Require signed OIDC claims even after a direct TLS-protected token exchange."""

    def complete_login(self, request, app, token, **kwargs):
        """A userinfo-only response cannot establish signed hosted-domain evidence.

        The one-use state record says which sign-in step this callback
        answers (#2343); the response's shape never decides it.
        """
        state = request.stewardship_oauth_state
        if state.get("data", {}).get("step") == SECOND_STEP:
            raise ImmediateHttpResponse(
                complete_address_check(request, app, kwargs["response"], state)
            )
        subject, email, hosted, authenticated_at = verified_claims(
            app, kwargs["response"], state
        )
        raise ImmediateHttpResponse(
            complete_identity(
                request, subject, email, hosted, authenticated_at=authenticated_at
            )
        )


def verified_claims(app, response, state):
    """The signed identity of a token response, checked against its state.

    Returns ``(sub, email, hosted, authenticated_at)``. Both sign-in steps
    check the ID token the same way: RS256 signature, audience, the state's
    own nonce, ``email_verified``, and the claim shapes. Anything else
    raises ``OAuth2Error``.
    """
    credential = response.get("id_token")
    if type(credential) is not str or not 0 < len(credential) <= 16384:
        raise OAuth2Error("Verified identity is unavailable.")
    try:
        if jwt.get_unverified_header(credential).get("alg") != "RS256":
            raise OAuth2Error("Verified identity is unavailable.")
        claims = _verify_and_decode(app, credential, verify_signature=True)
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise OAuth2Error("Verified identity is unavailable.") from None
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
    return claims["sub"], email, hosted, authenticated_at


def verified_authentication_time(claims, state):
    """Preserve signed provider authentication independently of local login time.

    The one-use nonce binds this exchange; auth_time, when present, records
    the provider's authentication, which may predate it. Google never sends
    auth_time (its discovery document lists no such claim), so the token's
    issue time stands in: the nonce binds this token to a sign-in started
    here within 15 minutes. The instant is recorded on the session and on
    privileged actions; no action requires it to be recent (#1547). An
    auth_time that is present is still validated.
    """
    now = database_now()
    authenticated = claims["auth_time"] if "auth_time" in claims else claims["iat"]
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
            return None, denial(status=429 if delay else 403, retry=delay, sign_in=True)
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
            return None, denial(status=429 if delay else 403, retry=delay, sign_in=True)
        # Persist the one-use receipt before provider I/O, independently of later
        # session saves by simultaneous browser tabs or callback requests.
        request.session.save()
        return state, response


LOGGER = logging.getLogger(__name__)

# The second sign-in step's name in its one-use state record (#2343).
SECOND_STEP = "addresses"

# The message and SQLSTATE of stewardship_portal_session_admission_v1's
# refusal (schema/functions.sql); only that refusal becomes a sign-in denial.
_ADMISSION_REFUSAL = "Admin session requires a current authorized principal"


def _session_admission_refused(error):
    """Say whether an IntegrityError is the Admin session insert guard's refusal."""
    cause = error.__cause__
    message = getattr(getattr(cause, "diag", None), "message_primary", None) or ""
    return getattr(cause, "sqlstate", None) == "23514" and message.startswith(
        _ADMISSION_REFUSAL
    )


def complete_identity(request, subject, email, hosted, *, authenticated_at):
    """Only a verified Google identity can be created or refresh its email claims.

    The recovery epoch comes from the one-use OAuth state the callback
    admitted; everything after that is the shared identity core,
    ``establish_identity``, which the LOCAL test sign-in also calls. Only
    this Google path may offer the check of the account's other verified
    addresses (#2343).
    """
    data = request.stewardship_oauth_state["data"]
    return establish_identity(
        request,
        subject,
        email,
        hosted,
        authenticated_at=authenticated_at,
        recovery_epoch=data["recovery_epoch"],
        # Checked again here, though the sign-in stored only a checked path.
        destination=admin_return_path(data.get("next")),
        offers_check=True,
    )


def _identity_counter(service, subject, email):
    """The verified-identity limiter counter of one account and address."""
    epoch = (
        PolicyEpoch.objects.order_by("-sequence")
        .values_list("sequence", flat=True)
        .first()
    )
    if epoch is None:
        raise ConfigError("Authentication policy is unavailable.")
    fingerprint = service.limiter.fingerprint("identity", subject + "\x00" + email)
    return fingerprint, Counter(
        f"admin_identity_{epoch}",
        fingerprint,
        service.limiter.limits.admin_identity,
        900,
    )


def _issue(request, service, user_id, *, authenticated_at, match=None):
    """Issue the Admin session of an authorized account; False when SQL refuses.

    ``match`` is the verified-address match the account signs in through,
    whose ID its ``admin_login`` records (#2343).
    """
    # A session this browser let go idle, expire or lose its authority ends
    # with that reason first, as admission records it; issue_admin then ends
    # a live one as admin_reauthenticated.
    authenticated_admin(request, store=service.store)
    try:
        issue_admin(
            request,
            user_id,
            store=service.store,
            authenticated_at=authenticated_at,
            match_id=None if match is None else match.pk,
        )
    except IntegrityError as error:
        # The SQL session guard (#306) re-checks the Admin rule at the
        # insert. A policy activation that removed it after
        # current_principal ends in the ordinary denial, not a 500; the
        # insert's savepoint has already rolled back. Any other integrity
        # violation is a bug and still fails loudly.
        if not _session_admission_refused(error):
            LOGGER.warning("Admin sign-in failed an unexpected database invariant")
            raise
        LOGGER.warning(
            "Admin sign-in denied: the SQL session guard found no current Admin rule"
        )
        request.session = AdminSessionStore()
        return False
    rotate_token(request)
    return True


def _pend(request, user, subject, destination, *, counted):
    """Keep the pending second sign-in step in the pre-sign-in session only.

    It holds the account's ``PortalUser`` ID and ``sub``, the checked
    ``next``, when the offer ends (15 minutes on) and whether the first
    step already counted this check against the identity limiter
    (``counted``), never in a URL, cookie value or form field (#2343).
    """
    request.session[address_matching.PENDING_KEY] = {
        "user": str(user.pk),
        "sub": subject,
        "next": destination,
        "until": int(database_now().timestamp()) + address_matching.PENDING_SECONDS,
        "counted": counted,
    }


def _check_counter(pending, counter):
    """The identity counter a second step's refusal counts against, or None.

    One failed check counts once against the identity limiter (#2374): the
    first-check page was already counted as the first step's refusal, so
    its second step's refusal counts only per source. The 30-day "again"
    page counts nothing, so its second step's refusal is the one count.
    """
    return None if pending.get("counted") is True else counter


def _take_pending(request, data):
    """Remove and return this browser's pending second step for ``data``.

    The step is offered once per sign-in, so it is removed whatever comes
    next. None when there is none, it expired, or its account and ``sub``
    are not the ones the state record (``data``) names.
    """
    pending = request.session.pop(address_matching.PENDING_KEY, None)
    if (
        not isinstance(pending, dict)
        or type(pending.get("until")) is not int
        or pending["until"] < database_now().timestamp()
        or not isinstance(data.get("user"), str)
        or not isinstance(data.get("sub"), str)
        or pending.get("user") != data["user"]
        or not secrets.compare_digest(str(pending.get("sub")), data["sub"])
    ):
        return None
    return pending


def _check_page(request, email, due):
    """The 30-day "again" form, which records and counts nothing (#2343)."""
    response = login_denial(
        admin=True,
        status=200,
        kind="address_check",
        account=email,
        csrf_token=get_token(request),
        due=due,
    )
    return response


def establish_identity(
    request,
    subject,
    email,
    hosted,
    *,
    authenticated_at,
    recovery_epoch,
    destination=ADMIN_HOME,
    offers_check=False,
):
    """The post-verification identity core shared by every Admin sign-in path.

    The caller has already verified who is signing in: the Google callback
    with signed OIDC claims, or the LOCAL test sign-in with its one-time
    token. This function does everything after that, identically for both:
    the identity rate limiter, the recovery-epoch check (``recovery_epoch``
    is the epoch the sign-in started under, and must still be current),
    ``PortalUser`` get-or-create and the disabled check, current-policy
    authorization, ``issue_admin``, the SQL session guard's
    ``IntegrityError`` handling and the CSRF rotation. Neither path may call
    ``issue_admin`` on its own, so sessions, audit and login rules cannot
    drift apart.

    A new session replaces whatever the browser held, the CSRF secret
    rotates and the browser goes to ``destination``: the Admin home, or
    the Admin page an ended session was sent to sign in from (#2058),
    which the caller has passed through ``admin_return_path``. There is no
    step-up sign-in that keeps a session (#1547).

    ``offers_check`` (the Google callback only) applies sign-in through
    another verified address while the deployment allows it (#2343): the
    refresh then follows ``address_matching.refresh``, under the users'
    ordering lock taken before the portal user row's lock, and a sign-in
    address with no user shows the Check your other email addresses page
    instead of "No access with this account". With the setting off this is
    exactly the sign-in it always was.
    """
    service = runtime()
    fingerprint, counter = _identity_counter(service, subject, email)
    delay = service.limiter.counters([counter])
    if delay:
        record_failure(request, identity=fingerprint, counter=counter)
        return denial(status=429, retry=delay)
    check = offers_check and address_matching.enabled()
    refreshed = address_matching.Refresh()
    if check:
        # A new first step replaces any earlier pending second step.
        request.session.pop(address_matching.PENDING_KEY, None)
    with transaction.atomic():
        if recovery_epoch != revocation_epoch():
            return denial(sign_in=True)
        if check:
            address_matching.take_users_lock()
        user, created = PortalUser.objects.select_for_update().get_or_create(
            google_subject=subject,
            defaults=dict(
                email=email,
                sign_in_email=email,
                hosted_domain=hosted,
                verified_at=database_now(),
            ),
        )
        if check:
            refreshed = address_matching.refresh(
                service.store, user, created=created, email=email, hosted=hosted
            )
        elif not created:
            # The account acts as its own sign-in address: with the setting
            # off no verified-address match is used (#2343).
            PortalUser.objects.filter(pk=user.pk).update(
                email=email,
                sign_in_email=email,
                hosted_domain=hosted,
                verified_at=database_now(),
                version=F("version") + 1,
            )
        principal = None if user.disabled else current_principal(service.store, user.pk)
        if (
            principal is not None
            and principal.roles
            and not _issue(
                request,
                service,
                user.pk,
                authenticated_at=authenticated_at,
                match=refreshed.match,
            )
        ):
            principal = None
    if principal is None or not principal.roles:
        if refreshed.offer == "again":
            # A match it routinely re-checks: not a refusal.
            _pend(request, user, subject, destination, counted=False)
            return _check_page(request, email, refreshed.due)
        # Google verified this account and policy refused it, so the audit
        # entry names the account, bounded per account and deployment-wide
        # (#953), in place of the anonymous sample. Limiter accounting runs
        # first so it still counts the attempt if the audit write fails.
        delay = record_failure(
            request, identity=fingerprint, counter=counter, sampled=False
        )
        record_account_refusal(user.pk)
        if refreshed.offer == "first":
            _pend(request, user, subject, destination, counted=True)
        # Only this branch names the address: Google verified it on this
        # callback, so the person seeing it is the one who controls it.
        return denial(
            status=429 if delay else 403,
            retry=delay,
            sign_in=True,
            account=(request, email),
            check={} if refreshed.offer == "first" else None,
        )
    service.limiter.clear(counter)
    return HttpResponseRedirect(destination)


def complete_address_check(request, app, response, state):
    """The second sign-in step's callback (#2343): check, revoke, then decide.

    The state record names the second step, so this browser's pre-sign-in
    session must hold the pending step for the same account and ``sub``.
    The ID token is verified as the first step's is and must carry the same
    ``sub``; the granted scopes must include the address and profile
    scopes, or the person declined; the People API is asked once. The
    access token is revoked whatever happened, before anything is decided.
    Any failure here (no pending step, the setting turned off, no ID token,
    another ``sub``, a timeout or error from Google) raises ``OAuth2Error``,
    which allauth hands to ``GoogleBoundary.on_authentication_error``:
    "Sign-in didn't finish", leaving a standing match as it was.
    """
    data = state["data"]
    pending = _take_pending(request, data)
    access = response.get("access_token")
    try:
        if pending is None or not address_matching.enabled():
            raise OAuth2Error("The address check is not pending.")
        subject, email, hosted, authenticated_at = verified_claims(app, response, state)
        if not secrets.compare_digest(subject, pending["sub"]):
            # The person chose another Google account at Google.
            raise OAuth2Error("Verified identity is unavailable.")
        addresses = (
            address_matching.fetch_addresses(access)
            if address_matching.granted(response.get("scope"))
            else None
        )
    except address_matching.GoogleUnavailable:
        raise OAuth2Error("Google is unavailable.") from None
    finally:
        address_matching.revoke(access)
    return _decide_check(
        request,
        pending,
        subject,
        email,
        hosted,
        authenticated_at=authenticated_at,
        addresses=addresses,
        recovery_epoch=data["recovery_epoch"],
    )


def declined_at_google(request, state):
    """A cancel at Google's second-step consent screen: the Declined outcome.

    Without its pending step this is an ordinary cancelled sign-in. The
    account was verified on the first callback of this same pending step,
    in this browser, at most 15 minutes ago, so its page names that
    sign-in address.
    """
    try:
        pending = _take_pending(request, state.get("data", {}))
        if pending is None or not address_matching.enabled():
            return denial(sign_in=True)
        service = runtime()
        user = PortalUser.objects.filter(
            pk=pending["user"], google_subject=pending["sub"], disabled=False
        ).first()
        if user is None:
            return denial(sign_in=True)
        fingerprint, counter = _identity_counter(
            service, pending["sub"], user.sign_in_email
        )
        return _refused_check(
            request,
            user.pk,
            user.sign_in_email,
            "declined",
            fingerprint,
            _check_counter(pending, counter),
        )
    except (LimiterUnavailable, ConfigError):
        return denial(status=503, retry=5)


def _refused_check(request, user_id, email, kind, fingerprint, counter):
    """A check that gave no access: counted, named within bounds, and its line."""
    delay = record_failure(
        request, identity=fingerprint, counter=counter, sampled=False
    )
    record_account_refusal(user_id, check=kind)
    return denial(
        status=429 if delay else 403,
        retry=delay,
        sign_in=True,
        account=(request, email),
        line=kind,
    )


def _decide_check(
    request,
    pending,
    subject,
    email,
    hosted,
    *,
    authenticated_at,
    addresses,
    recovery_epoch,
):
    """Record a completed check's outcome and sign the account in if it may.

    One transaction: the users' ordering lock, then the account's row lock,
    the refresh from the second ID token, the outcome's match writes
    (``address_matching.decide``) and, for a successful one, the session.
    A re-check records ``admin_address_checked`` with the match's ID beside
    it; a refusing outcome records it within the sign-in refusal bounds
    after the transaction, as the first step's refusal is.
    """
    service = runtime()
    fingerprint, counter = _identity_counter(service, subject, email)
    delay = service.limiter.counters([counter])
    if delay:
        record_failure(request, identity=fingerprint, counter=counter)
        return denial(status=429, retry=delay)
    signed_in = False
    with transaction.atomic():
        if recovery_epoch != revocation_epoch():
            return denial(sign_in=True)
        address_matching.take_users_lock()
        user = (
            PortalUser.objects.select_for_update()
            .filter(pk=pending["user"], google_subject=subject)
            .first()
        )
        if user is None:
            return denial(sign_in=True)
        address_matching.refresh_second(user, email=email, hosted=hosted)
        outcome = (
            # A disabled account is refreshed and offered nothing.
            address_matching.Outcome("disabled")
            if user.disabled
            else address_matching.decide(
                service.store, user, email=email, addresses=addresses
            )
        )
        if outcome.kind == "rechecked":
            record_action(
                Action.ADMIN_ADDRESS_CHECKED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=user.pk,
                subject_id=outcome.match.pk,
                context={"address_check": "rechecked", "match_id": outcome.match.pk},
            )
        if outcome.signs_in:
            principal = current_principal(service.store, user.pk)
            signed_in = bool(principal.roles) and _issue(
                request,
                service,
                user.pk,
                authenticated_at=authenticated_at,
                match=outcome.match,
            )
    if signed_in:
        service.limiter.clear(counter)
        return HttpResponseRedirect(admin_return_path(pending.get("next")))
    if outcome.kind in ADDRESS_CHECK_LINES:
        return _refused_check(
            request,
            user.pk,
            email,
            outcome.kind,
            fingerprint,
            _check_counter(pending, counter),
        )
    # Signed in but refused afterwards (the SQL session guard), or disabled.
    delay = record_failure(
        request,
        identity=fingerprint,
        counter=_check_counter(pending, counter),
        sampled=False,
    )
    record_account_refusal(user.pk)
    return denial(
        status=429 if delay else 403,
        retry=delay,
        sign_in=True,
        account=(request, email),
    )


@require_http_methods(["GET", "HEAD", "POST"])
def login(request):
    """Only a CSRF-protected POST initiates authentication; ignore dynamic scopes.

    The sign-in returns to the Admin home, or to the Admin page in the
    form's ``next`` (``admin_return_path`` accepts only a same-origin Admin
    page; anything else means home). A page load whose session had ended
    is sent here with that page as ``next`` and the fixed ``ended=1`` flag,
    which shows a short notice (#2058). A refused sign-in's retry link is
    plain ``/admin/login``, so that sign-in opens the Admin home. Google is
    asked only to let the person choose an account
    (``prompt=select_account``); it ignores ``max_age``, so none is sent
    (#1547).
    """
    try:
        service = runtime()
        if request.method != "POST":
            destination = admin_return_path(request.GET.get("next"))
            return render(
                request,
                "stewardship/login.html",
                {
                    "ended": request.GET.get("ended") == "1",
                    # A cancelling Sign out during setup (#2272): fixed flags.
                    "setup_cancelled": request.GET.get("setup")
                    in {"cancelled", "cancelled-finishing"},
                    "setup_finishing": request.GET.get("setup")
                    == "cancelled-finishing",
                    "next": None if destination == ADMIN_HOME else destination,
                },
            )
        limiter = service.limiter
        counter = ip_counter(limiter, request.client_address, initiation=True)
        delay = limiter.counters([counter])
        if delay:
            return denial(status=429, retry=delay)
        limiter.counters([counter], failure=True)
        nonce = secrets.token_urlsafe(32)
        if "principal" not in request.session:
            request.session.set_expiry(database_now() + timedelta(minutes=15))
        provider = get_adapter(request).get_provider(request, "google")
        return provider.redirect(
            request,
            process="login",
            next_url=ADMIN_HOME,
            data={
                "nonce": nonce,
                "recovery_epoch": revocation_epoch(),
                "initiated_at": int(database_now().timestamp()),
                # Where the callback sends the browser: a checked Admin page.
                "next": admin_return_path(request.POST.get("next")),
            },
            scope=["openid", "email"],
            auth_params={
                "nonce": nonce,
                "prompt": "select_account",
            },
        )
    except (LimiterUnavailable, ConfigError):
        return denial(status=503, retry=5)


@require_POST
def login_addresses(request):
    """Check my other addresses: send the browser to Google a second time.

    The CSRF-protected POST of the Check your other email addresses page
    (#2343). It needs this browser's pending second step, within its 15
    minutes, and the setting on; otherwise "Sign-in didn't finish". It is
    limited as an OAuth initiation, and its one-use state, nonce and PKCE
    pair are its own, with the same callback URL. Google is asked for the
    email-address scope with ``include_granted_scopes``, the account's
    sign-in address as ``login_hint`` and ``prompt=consent``; ``openid`` so
    an ID token binds the answer to the first step's account, and
    ``profile`` because people/me needs it. The pending step stays in the
    session until the callback takes it.
    """
    try:
        service = runtime()
        pending = request.session.get(address_matching.PENDING_KEY)
        if (
            not address_matching.enabled()
            or not isinstance(pending, dict)
            or type(pending.get("until")) is not int
            or pending["until"] < database_now().timestamp()
        ):
            return denial(sign_in=True)
        limiter = service.limiter
        counter = ip_counter(limiter, request.client_address, initiation=True)
        delay = limiter.counters([counter])
        if delay:
            return denial(status=429, retry=delay)
        limiter.counters([counter], failure=True)
        user = PortalUser.objects.filter(
            pk=pending.get("user"), google_subject=pending.get("sub"), disabled=False
        ).first()
        if user is None:
            return denial(sign_in=True)
        nonce = secrets.token_urlsafe(32)
        provider = get_adapter(request).get_provider(request, "google")
        return provider.redirect(
            request,
            process="login",
            next_url=ADMIN_HOME,
            data={
                "nonce": nonce,
                "recovery_epoch": revocation_epoch(),
                "initiated_at": int(database_now().timestamp()),
                "next": admin_return_path(pending.get("next")),
                "step": SECOND_STEP,
                "user": str(user.pk),
                "sub": user.google_subject,
            },
            scope=list(address_matching.SECOND_STEP_SCOPES),
            auth_params={
                "nonce": nonce,
                "include_granted_scopes": "true",
                "login_hint": user.sign_in_email,
                "prompt": "consent",
            },
        )
    except (LimiterUnavailable, ConfigError, ValueError):
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
    """CSRF-protected logout revokes authority and drops only the Admin cookie.

    A command session's key presented here is refused as tampering first,
    which also ends its automation session (``misused``).
    """
    from .automation_sessions import MARKER, refuse_web_session

    if request.session.get(MARKER) is not None:
        refuse_web_session(request.session)
        request.session = AdminSessionStore()
        return HttpResponseRedirect("/admin/login")
    notice = _cancel_setup_on_logout(request)
    end_admin(request)
    return HttpResponseRedirect(
        "/admin/login" + (f"?setup=cancelled{notice}" if notice is not None else "")
    )


def _cancel_setup_on_logout(request):
    """Cancel the owned setup attempt a Sign out asked to cancel (#2272).

    Only a POST field ``cancel_setup`` (the attempt's id) asks. Returns ""
    after a cancelled collecting attempt and "-finishing" after a finishing
    one (the suffix of the sign-in page's fixed ``setup`` flag), or None
    when nothing was cancelled. Sign out is an escape hatch, so a refused or
    failed cancellation never blocks it: the session still ends and the
    scheduler expires the attempt with reason ``session``, as for a plain
    logout. Ownership is checked by the cancellation itself.
    """
    raw = request.POST.get("cancel_setup")
    if not raw:
        return None
    from parishkit.stewardship.storage import StaleRecordError

    from .setup_cancellation import cancel_for_sign_out

    # The cancellation authenticates the request, which may rotate a stale
    # session (replacing request.session) and then roll that back when the
    # attempt is refused. Always put the browser's own session back, so the
    # sign-out revokes the row the cookie names, never a rolled-back new one.
    original = request.session
    try:
        kind = cancel_for_sign_out(request, runtime(), UUID(raw))
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        LookupError,
        PermissionError,
        StaleRecordError,
        ValueError,
    ):
        kind = None
    except Exception:
        # Sign out must always end the session, whatever went wrong.
        LOGGER.exception("Setup cancellation during sign-out failed.")
        kind = None
    finally:
        request.session = original
    return {"collecting": "", "finishing": "-finishing"}.get(kind)


def _review_counts(request, caller, principal, store, live):
    """The Review work cards that changed since the page drew them (#2174).

    ``live`` is the cards a review region drew (``review_live.drawn``). The
    counts are read as My review work reads them (``review_work.observe``),
    in one snapshot, and the panels built from the viewer's menu, as both
    My review work and Admin Home draw them. With no card changed the answer
    is empty and nothing is recorded; otherwise it is the panels, and the
    dashboard view is recorded once, marked automatic (#1620).
    """
    from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
    from parishkit.stewardship.audit.services import record_action
    from parishkit.stewardship.web.review_live import item_key, item_token

    from . import review_work
    from .admin_context import portal_chrome

    observed = review_work.observe(principal, store)
    if observed is None:
        return denial(status=503, retry=5)
    config, review, now = observed
    request._stewardship_display_now = now
    request._stewardship_display_configuration = config
    chrome = portal_chrome(request).get("admin_chrome") or {}
    panels = review_work.panels(chrome, review) if review["campaign"] else []
    changed = sum(
        1
        for panel in panels
        for card in panel.cards
        if live.get(item_key(card.label, panel.stream), item_token(card))
        != item_token(card)
    )
    body = b""
    if changed:
        body = render_to_string(
            "stewardship/review-work-panels.html",
            {"panels": panels, "nested": not review_work.lands_here(principal)},
        ).encode()
        with transaction.atomic():
            if authenticated_admin(caller, store=store, read_only=True) != principal:
                return denial(status=403)
            record_action(
                Action.DASHBOARD_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=principal.identity,
                parish_id=config.active_configuration.parish.pk,
                campaign_id=config.current_campaign_id,
                context={"outcome": Outcome.SUCCEEDED, "automatic": True},
            )
    response = HttpResponse(body, content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store"
    return response


@require_safe
def index(request):
    """Show Home, or My review work, under fresh, capability-filtered authorization.

    Staff and Ministry leaders sign in to My review work instead of Home
    (review pipeline spec, "Role views", decision 8; ``review_work``); an
    Administrator keeps Home. Both are the dashboard view for the audit.
    """
    from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
    from parishkit.stewardship.audit.services import record_action
    from parishkit.stewardship.web import review_live

    from . import review_work
    from .admin_caller import AdminCaller
    from .admin_dashboard import observe
    from .sessions import automatic_read

    try:
        # The Review work cards' review region check (#2174): its drawn
        # cards, answered by ``_review_counts``. It never extends the
        # sign-in, whatever its headers say (``review_live.checking``).
        live = review_live.drawn(request)
    except ValueError:
        return denial(status=400)
    passive = automatic_read(request) or review_live.checking(request)
    try:
        service = runtime()
        caller = AdminCaller.from_request(request)
        principal = authenticated_admin(
            caller, store=service.store, activity=not passive
        )
        if live is not None and principal is not None:
            return _review_counts(request, caller, principal, service.store, live)
        if principal is None:
            # With the ended flag when the browser still had a session
            # (#2058), as every other Admin page load does.
            from parishkit.stewardship.web.error_pages import sign_in_address

            return HttpResponseRedirect(sign_in_address(request, ended=True))
        landing = review_work.lands_here(principal)
        if landing:
            observed = review_work.observe(principal, service.store)
        else:
            observed = observe(principal, service.store, home=True)
        if observed is None:
            return denial(status=503, retry=5)
        config, data, now = observed
        # The chrome renders right after the snapshot; it reuses this
        # instant, read inside that snapshot, instead of another clock query.
        request._stewardship_display_now = now
        # Presentation only: this exact projection is already verified and
        # captured with the summary in the same snapshot. Rendering happens
        # after release; chrome must use that observation, not independently
        # choose a newer projection for the same response.
        request._stewardship_display_configuration = config
        if not landing and data.get("review"):
            # Home's Review work counts ride on the menu's open-count
            # statement (review_work.with_counts), which reads this.
            request._stewardship_review_flags = data["review"]["flags"]
        response = render(
            request,
            "stewardship/my-review-work.html" if landing else "stewardship/home.html",
            {
                "principal": principal,
                "configuration": config,
                ("review" if landing else "dashboard"): data,
            },
        )
        with transaction.atomic():
            if (
                authenticated_admin(caller, store=service.store, read_only=True)
                != principal
            ):
                return denial(status=403)
            # A rendered-but-revoked or failed response never claims a successful
            # disclosure. The request boundary owns failure/denial diagnostics.
            record_action(
                Action.DASHBOARD_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=principal.identity,
                parish_id=config.active_configuration.parish.pk,
                campaign_id=config.current_campaign_id,
                context={"outcome": Outcome.SUCCEEDED},
            )
        response["Cache-Control"] = "no-store"
        return response
    except (LimiterUnavailable, ConfigError, DatabaseError):
        return denial(status=503, retry=5)
