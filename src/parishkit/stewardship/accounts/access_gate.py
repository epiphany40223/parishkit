"""Shared setup/maintenance routing; object-specific authorization stays in views."""

import re

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin

from parishkit.config import ConfigError
from parishkit.stewardship.web.namespaces import is_admin
from parishkit.stewardship.web.security import login_denial

from . import family_maintenance
from .admin_context import greyed_page
from .authentication import runtime
from .limiting import LimiterUnavailable
from .local_sign_in import PATH as LOCAL_SIGN_IN
from .models import SystemConfiguration
from .role_refusal import signed_out_response
from .sessions import authenticated_admin

AUTH_ROUTES = frozenset(
    {
        "/admin/login",
        "/admin/oauth/start",
        "/admin/oauth/callback",
        "/admin/logout",
        # The shared inactivity warning must work on every Admin page in every
        # portal state; these views authenticate the session themselves.
        "/admin/session/status",
        "/admin/session/renew",
        # The LOCAL test sign-in (#476) stands in for Google sign-in there, or
        # sits beside it with a real OAuth client (#1399), and is
        # needed most before setup completes or during restore review; the
        # view itself answers 404 under every other profile.
        LOCAL_SIGN_IN,
    }
)


def status_page(request, *, kind, admin=False):
    """Only fixed messages and retry/logout controls cross an unavailable gate."""
    if kind not in {"setup", "maintenance"}:
        raise ValueError("Unknown portal availability state.")
    response = render(
        request,
        "stewardship/availability.html",
        {"setup": kind == "setup", "admin": admin},
        status=503,
    )
    response.stewardship_safe_error = True
    response["Retry-After"] = "5"
    return response


class AccessGateMiddleware(MiddlewareMixin):
    """Gate resolved application endpoints without exposing internal/unknown paths."""

    def process_view(self, request, view_func, view_args, view_kwargs):
        """Direct HTML and partial/POST endpoints share the same state admission."""
        admin = is_admin(request)
        family = (
            request.path_info == "/"
            or request.path_info.startswith("/family/")
            or request.path_info.startswith("/access/")
        )
        if (not admin and not family) or request.path_info in AUTH_ROUTES:
            return None
        if request.path_info == "/admin/setup/finishing":
            # The dedicated view authenticates only the original setup login
            # against its still-applied bootstrap predecessor. No other route
            # may use that narrow selected-but-unapplied cancellation exception.
            return None
        try:
            service = runtime()
            configured = service.configured()
            restored = SystemConfiguration.objects.values_list(
                "restore_review_required", flat=True
            ).first()
            if (
                configured
                and not restored
                and request.path_info != "/admin/maintenance"
                and not _setup_path(request.path_info)
            ):
                # Ordinary views own current-policy authentication, including
                # expiry revocation and privilege-change cookie rotation. This
                # routing-only middleware must neither repeat that work nor
                # reject a session before its owning view can rotate it.
                # The one exception is an Administrator's maintenance switch,
                # which closes Family routes before any Family view runs.
                if family and family_maintenance.gates(request):
                    state = family_maintenance.current_state()
                    if state.closed:
                        return family_maintenance.family_response(request, state)
                # A menu page its menu greys out right now says why, in the
                # menu's words, instead of its view's own refusal (#1128).
                # Read-only and presentation only; see ``greyed_page``.
                if admin:
                    return greyed_page(request, service.store)
                return None
            if admin:
                principal = authenticated_admin(request, store=service.store)
                if principal is None:
                    # Before setup or during a restore review, the shared
                    # signed-out refusal, as on every other Admin page
                    # (#2062): the browser-error middleware sends a page
                    # load to sign-in with the page as ``next``, a form
                    # POST gets its one sign-in link, and scripts keep the
                    # typed 403 the session-ended dialog reads.
                    return signed_out_response()
            if restored:
                return self._unavailable(
                    request, "maintenance", admin, principal if admin else None
                )
            if not configured:
                return self._unavailable(
                    request, "setup", admin, principal if admin else None
                )
            if (
                _setup_path(request.path_info)
                or request.path_info == "/admin/maintenance"
            ):
                return HttpResponseRedirect("/admin/")
        except (ConfigError, LimiterUnavailable):
            # Keep the missing-runtime scaffold closed, with a stable retry URL.
            # An outage reads as temporary on Admin and Family routes alike.
            response = login_denial(admin=admin, status=503, kind="unavailable")
            response["Retry-After"] = "5"
            return response
        return None

    def _unavailable(self, request, kind, admin, principal):
        """An Admin may reach only the matching owning workflow and its subroutes."""
        if admin and "administrator" in principal.roles:
            destination = "/admin/" + kind
            if request.path_info == destination or (
                kind == "setup"
                and (
                    _setup_path(request.path_info)
                    or (
                        request.method in {"GET", "HEAD"}
                        and BACKGROUND_READS.fullmatch(request.path_info)
                    )
                )
            ):
                return None
            if request.method in {"GET", "HEAD"}:
                return HttpResponseRedirect(destination)
        return status_page(request, kind=kind, admin=admin)


# Read-only background-work views an Administrator may open during initial
# setup, to watch the setup's own load: Background work and one task's page
# under /admin/system/background/ and the JSON reads scripts poll. Commands
# under these paths are POSTs and stay gated; each view still rechecks the
# Background-work capability.
_TASK = r"[0-9a-f-]{36}"
BACKGROUND_READS = re.compile(
    rf"/admin/system/background/(?:{_TASK}/)?"
    rf"|/admin/background/(?:counts|tasks|tasks/{_TASK})"
)


def _setup_path(path):
    """Match the exact setup namespace, never a similarly prefixed Admin route."""
    return path == "/admin/setup" or path.startswith("/admin/setup/")
