"""Open form: Staff enter a response for a Family on the phone (#529).

Staff and Administrators who may see Family codes open the Family form for a
Family from its timeline or the Family directory. Open form used to be a link
with the Family's code in its fragment; the form then could not tell a
response Staff typed in from the Family's own. Now it is a two-step hand-off:

1. ``open_form`` (Admin namespace) is a CSRF-protected POST. It re-checks the
   signed-in Admin's session and capabilities, the Production mode, the
   current campaign and the Family, then stores a hand-off in Valkey under
   the SHA-256 of a fresh 256-bit secret, valid for ``HANDOFF_SECONDS``, and
   audits ``family_form_opened``. Its response, opened in a new tab, posts
   the secret to the Family namespace at once (``open-form-v1.js``). The
   secret travels only in that POST body: never in a URL, log or audit.
2. ``assisted`` (Family namespace) takes the hand-off with one atomic
   get-and-delete, so it is single-use and cannot be replayed to start a
   Family session later. It checks that the Admin's session is still live and
   that the Admin may still see Family codes, then signs the browser in as
   the Family through the ordinary code sign-in (``issue_family``), whose
   SQL re-proves the Family's code. The Family session records who opened it,
   and a submission in that session is marked ``entered_by_id``.

Neither step creates, replaces or cancels a Family code or link: emailed
Production codes and links keep working exactly as before.
"""

import hashlib
import hmac
import json
import re
import secrets
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from redis.exceptions import RedisError

from parishkit.config import ConfigError
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_identity import code_context

from .cryptography import CryptographicError
from .limiting import LimiterUnavailable
from .policy import Capability, allows, current_principal
from .runtime_models import SystemConfiguration
from .session_policy import ADMIN_IDLE

# A hand-off is used within a second or two of the click; a minute allows a
# slow browser without leaving a usable secret around.
HANDOFF_SECONDS = 60
NAMESPACE = "stewardship:auth:v1"
# secrets.token_urlsafe(32): 43 URL-safe base64 characters.
SECRET = re.compile(r"[A-Za-z0-9_-]{43}")
# The Family session's record of the Admin who opened it (see ``mark``).
SESSION_KEY = "assisted"
# A second secret, in a cookie only the hand-off's own path receives, binds
# the hand-off to the browser that asked for it.
BINDING_COOKIE = "pk_assist_binding"
BINDING_PATH = "/family/assisted"
# Atomic get-and-delete within the web Valkey ACL, which has no GETDEL.
CONSUME = """
local value = redis.call('GET', KEYS[1])
if value then redis.call('DEL', KEYS[1]) end
return value
"""


def _digest(secret):
    """The SHA-256 of a secret, in hex: what Valkey keeps instead of it."""
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def _key(secret):
    """The Valkey key of a hand-off: a digest, so Valkey never holds the secret."""
    return f"{NAMESPACE}:assisted-entry:{_digest(secret)}"


def start(client, record):
    """Store ``record`` under a fresh secret for ``HANDOFF_SECONDS``; return it."""
    value = json.dumps(record, sort_keys=True, separators=(",", ":"))
    for _ in range(4):
        secret = secrets.token_urlsafe(32)
        if client.set(_key(secret), value, ex=HANDOFF_SECONDS, nx=True):
            return secret
    raise ConfigError("No Open form hand-off could be stored.")


def consume(client, secret):
    """The hand-off for ``secret``, deleted as it is read; None if none.

    Malformed secrets and stored values are refused without detail.
    """
    if type(secret) is not str or SECRET.fullmatch(secret) is None:
        return None
    raw = client.eval(CONSUME, 1, _key(secret))
    if raw is None:
        return None
    try:
        record = json.loads(raw)
        value = {
            name: UUID(record[name])
            for name in ("admin", "session", "family", "campaign")
        }
        value["binding"] = str(record["binding"])
        return value
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None


def bound(record, binding):
    """Whether ``binding`` (the cookie's value) is the hand-off's own."""
    if type(binding) is not str or SECRET.fullmatch(binding) is None:
        return False
    return hmac.compare_digest(record["binding"], _digest(binding))


def may_open(principal):
    """Open form needs the Family timeline's report access and Family codes."""
    return allows(principal, Capability.CAMPAIGN_REPORT) and allows(
        principal, Capability.FAMILY_CODES
    )


def _refusal(request, *, status=403):
    """Render the plain Admin refusal for ``request``."""
    response = render(request, "stewardship/open-form-refused.html", status=status)
    response.stewardship_safe_error = True
    return response


@require_POST
def open_form(request, campaign_id, family_id):
    """Start a hand-off for one Family and post it to the Family form.

    Refused, with one plain page and no reason, unless the signed-in Admin may
    see Family codes now, the system is in Production, the campaign is the
    current one and the Family is in it with a code and may use the portal.
    """
    from .authentication import runtime
    from .sessions import authenticated_admin

    try:
        service = runtime()
        with transaction.atomic():
            principal = authenticated_admin(request, store=service.store, activity=True)
            if principal is None or not may_open(principal):
                return _refusal(request)
            system = SystemConfiguration.objects.select_related(
                "active_configuration__parish"
            ).get()
            if (
                system.mode != "production"
                or system.current_campaign_id != campaign_id
                or not FamilyCampaign.objects.filter(
                    pk=family_id,
                    campaign_id=campaign_id,
                    portal_eligible=True,
                    code_ciphertext__isnull=False,
                ).exists()
            ):
                return _refusal(request)
            binding = secrets.token_urlsafe(32)
            secret = start(
                service.limiter.client,
                {
                    "admin": str(principal.identity),
                    "session": str(request.portal_session.pk),
                    "family": str(family_id),
                    "campaign": str(campaign_id),
                    "binding": _digest(binding),
                },
            )
            # The hand-off is recorded with who opened which Family; the
            # secret and the code are never recorded.
            record_action(
                Action.FAMILY_FORM_OPENED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=principal.identity,
                subject_id=family_id,
                parish_id=system.active_configuration.parish.pk,
                campaign_id=campaign_id,
                context={"outcome": Outcome.SUCCEEDED},
            )
        response = render(
            request,
            "stewardship/open-form-handoff.html",
            {
                "secret": secret,
                "action": reverse("family:assisted"),
                # Where Back leads from the Family form (see open-form-v1.js).
                "return_url": reverse(
                    "admin:family_timeline", args=[campaign_id, family_id]
                ),
            },
        )
        response["Cache-Control"] = "no-store"
        # Only this browser can redeem the hand-off: the cookie goes to the
        # hand-off's path alone, never to script, and expires with it.
        response.set_cookie(
            BINDING_COOKIE,
            binding,
            max_age=HANDOFF_SECONDS,
            path=BINDING_PATH,
            secure=settings.SESSION_COOKIE_SECURE,
            httponly=True,
            samesite="Strict",
        )
        return response
    except (ConfigError, LimiterUnavailable, RedisError):
        return _refusal(request, status=503)


def _same_origin(request):
    """Whether this POST came from one of this origin's own pages.

    The hand-off arrives without a Family-namespace CSRF token (the page that
    posts it is an Admin page), so the check is same-origin, not merely
    same-site: the browser's Origin header must equal this request's exact
    scheme and host, as Django's own CSRF origin check requires, and
    ``Sec-Fetch-Site``, when sent, must say ``same-origin``. The single-use
    secret is the rest of the proof.
    """
    origin = request.headers.get("Origin")
    if request.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin":
        return False
    return origin == f"{request.scheme}://{request.get_host()}"


def _live_admin(record):
    """Whether the hand-off's Admin session is still live and may open forms.

    The session must be the same unrevoked row, within its idle and absolute
    deadlines, for the same Admin, whose current roles still allow Open form.
    """
    from .authentication import runtime
    from .models import PortalSession
    from .sessions import database_now

    row = PortalSession.objects.filter(
        pk=record["session"], principal_id=record["admin"], revoked_at__isnull=True
    ).first()
    if row is None:
        return False
    now = database_now()
    if now >= min(row.expires_at, row.last_activity_at + ADMIN_IDLE):
        return False
    try:
        principal = current_principal(runtime().store, record["admin"])
    except (ObjectDoesNotExist, ConfigError, PermissionError):
        # A removed or disabled portal user, or policy that cannot load.
        return False
    return may_open(principal)


def _family_code(service, record):
    """The hand-off Family's code, or None when the hand-off cannot be used.

    None for no hand-off, an Admin session or role that has ended, or a
    Family that is gone or has no code. The code is decrypted under the
    key-set lock, as the Family timeline does, and never leaves this call
    except into the ordinary code sign-in.
    """
    if record is None or not _live_admin(record):
        return None
    # The key-set lock is transaction-scoped: read and decrypt under it.
    with transaction.atomic():
        family = FamilyCampaign.objects.filter(
            pk=record["family"], campaign_id=record["campaign"]
        ).first()
        if family is None or not family.code_ciphertext:
            return None
        with key_set_lock(service.general, service.mac):
            return service.general.decrypt(
                family.code_ciphertext, context=code_context(family.pk)
            ).decode("ascii")


def _assist_refused(request):
    """The Family-side refusal: one page for every reason, never cached."""
    response = render(request, "stewardship/family-assist-refused.html", status=403)
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_POST
def assisted(request):
    """Redeem a hand-off: sign in as the Family, marked as opened by Staff.

    Every refusal (a used, expired or unknown hand-off, another site, a
    session or role that has ended, a Family no longer admitted) shows the
    same page. The hand-off is spent by the first attempt either way.
    """
    from .family_authentication import (
        _ip_counter,
        denied,
        issue_family,
        lookup,
    )
    from .family_authentication import runtime as family_runtime

    try:
        service = family_runtime()
        if not _same_origin(request):
            return _assist_refused(request)
        ip = _ip_counter(service, request.client_address)
        delay = service.limiter.counters([ip])
        if delay:
            return denied(status=429, retry=delay)
        record = consume(service.limiter.client, request.POST.get("handoff"))
        if record is not None and not bound(
            record, request.COOKIES.get(BINDING_COOKIE)
        ):
            record = None
        code = _family_code(service, record)
        identity = lookup(service, code=code) if code else None
        # Only the hand-off's Family, in Production: never a Testing sign-in.
        if (
            identity is not None
            and identity[0] == record["family"]
            and identity[2] == "production"
            and issue_family(
                request, service, identity, code=code, assisted_by=record["admin"]
            )
        ):
            response = HttpResponseRedirect("/family/")
        else:
            service.limiter.counters([ip], failure=True)
            response = _assist_refused(request)
        response.delete_cookie(BINDING_COOKIE, path=BINDING_PATH, samesite="Strict")
        return response
    except (LimiterUnavailable, ConfigError, RedisError, CryptographicError):
        return denied(status=503, retry=5)


def mark(request, row_id, admin_id):
    """Record on the new Family session who opened it, and audit that.

    Called by ``issue_family`` inside its sign-in transaction. The record
    names the session row it belongs to, so it marks only that session.
    """
    request.session[SESSION_KEY] = {"by": str(admin_id), "session": str(row_id)}
    AuditEvent.objects.create(
        event_type="family_assisted_login", subject_id=row_id, actor_id=admin_id
    )


def entered_by(request, session):
    """The Admin who opened this Family session through Open form, or None."""
    value = request.session.get(SESSION_KEY)
    if not isinstance(value, dict) or value.get("session") != str(session.pk):
        return None
    try:
        return UUID(value["by"])
    except (KeyError, TypeError, ValueError):
        return None
