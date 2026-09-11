"""Shared setup/maintenance routing; object-specific authorization stays in views."""

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin
from django.views.decorators.http import require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.web.security import login_denial

from .authentication import runtime
from .limiting import LimiterUnavailable
from .models import SystemConfiguration
from .sessions import authenticated_admin

AUTH_ROUTES = frozenset(
    {
        "/admin/login",
        "/admin/oauth/start",
        "/admin/oauth/callback",
        "/admin/logout",
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
        admin = request.path_info.startswith("/admin/")
        family = (
            request.path_info == "/"
            or request.path_info.startswith("/family/")
            or request.path_info.startswith("/access/")
        )
        if (not admin and not family) or request.path_info in AUTH_ROUTES:
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
                and request.path_info not in {"/admin/setup", "/admin/maintenance"}
            ):
                # Ordinary views own current-policy authentication, including
                # expiry revocation and privilege-change cookie rotation. This
                # routing-only middleware must neither repeat that work nor
                # reject a session before its owning view can rotate it.
                return None
            if admin:
                principal = authenticated_admin(request, store=service.store)
                if principal is None:
                    return HttpResponseRedirect("/admin/login")
            if restored:
                return self._unavailable(
                    request, "maintenance", admin, principal if admin else None
                )
            if not configured:
                return self._unavailable(
                    request, "setup", admin, principal if admin else None
                )
            if request.path_info in {"/admin/setup", "/admin/maintenance"}:
                return HttpResponseRedirect("/admin/")
        except (ConfigError, LimiterUnavailable):
            # Keep the missing-runtime scaffold closed, with a stable retry URL.
            response = login_denial(admin=admin, status=503)
            response["Retry-After"] = "5"
            return response
        return None

    def _unavailable(self, request, kind, admin, principal):
        """An Admin may reach only the matching placeholder/future owner workflow."""
        if admin and "administrator" in principal.roles:
            destination = "/admin/" + kind
            if request.path_info == destination:
                return None
            if request.method in {"GET", "HEAD"}:
                return HttpResponseRedirect(destination)
        return status_page(request, kind=kind, admin=admin)


@require_safe
def setup(request):
    """ADM-02 owns the transactional wizard; this route offers no partial writes."""
    return status_page(request, kind="setup", admin=True)


@require_safe
def maintenance(request):
    """ADM-06 owns restore controls; authentication alone never releases this gate."""
    return status_page(request, kind="maintenance", admin=True)
