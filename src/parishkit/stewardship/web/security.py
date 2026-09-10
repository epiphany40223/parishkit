"""Trusted ingress, uniform private errors and browser security headers."""

from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import HttpResponse
from django.template.loader import render_to_string

from parishkit.stewardship.deployment import DeploymentProfile

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; "
    "form-action 'self' https://accounts.google.com; base-uri 'none'; "
    "frame-ancestors 'none'; object-src 'none'"
)
FORWARDED = (
    "HTTP_FORWARDED",
    "HTTP_X_FORWARDED_FOR",
    "HTTP_X_FORWARDED_PROTO",
    "HTTP_X_FORWARDED_HOST",
)


def browser_settings(deployment):
    """Validated startup applies this profile without enabling production itself.

    Internal health is exempt from redirects, not ingress authorization. Do not
    infer trust from a Forwarded header or use Django's unchecked proxy shortcut.
    """
    if not isinstance(deployment.profile, DeploymentProfile):
        raise ValueError("A canonical deployment profile is required.")
    production = deployment.profile is DeploymentProfile.PRODUCTION
    origin = urlsplit(deployment.public_origin)
    if (
        not origin.hostname
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
        or origin.path not in {"", "/"}
        or origin.scheme not in {"https", "http"}
        or (production and origin.scheme != "https")
    ):
        raise ValueError("A valid secure canonical production origin is required.")
    return {
        "ALLOWED_HOSTS": [origin.hostname],
        "CSRF_TRUSTED_ORIGINS": [deployment.public_origin.rstrip("/")],
        "SESSION_COOKIE_SECURE": production,
        "CSRF_COOKIE_SECURE": production,
        "SECURE_SSL_REDIRECT": production,
        "SECURE_HSTS_SECONDS": 31536000 if production else 0,
        "SECURE_HSTS_INCLUDE_SUBDOMAINS": False,
        "SECURE_HSTS_PRELOAD": False,
        "SECURE_PROXY_SSL_HEADER": None,
        "SECURE_REDIRECT_EXEMPT": [r"health/(live|ready)$", r"metrics$"],
    }


def private_response(message, *, status=200):
    """Use only caller-owned messages; never reflect paths, queries or exceptions."""
    response = HttpResponse(
        message, status=status, content_type="text/plain; charset=utf-8"
    )
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def login_denial(*, admin=False, status=403):
    """Accessible uniform error with a fixed retry route, never an input redirect."""
    response = HttpResponse(
        render_to_string(
            "stewardship/denied.html",
            {
                "retry_path": "/admin/login" if admin else "/",
            },
        ),
        status=status,
    )
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    return response


def error_response(request, *, status):
    """Internal explicit-status helper, not a Django handler4xx callback."""
    messages = {
        400: "Invalid request.\n",
        403: "Access is not authorized.\n",
        404: "Not Found\n",
        500: "The request could not be completed.\n",
    }
    return private_response(messages[status], status=status)


def csrf_failure(request, reason=""):
    """Do not disclose the supplied origin, cookie, referer or framework reason."""
    return error_response(request, status=403)


def _in_networks(address, networks):
    """Configuration errors fail closed, never implicitly trust an arbitrary peer."""
    return any(address in ip_network(network, strict=True) for network in networks)


def resolve_client(request):
    """Trust exactly one normalized forwarding hop from configured proxy peers.

    REMOTE_ADDR is supplied by the WSGI server, never by a browser header.
    Production provisioning validates the peer networks and hop count. Strip
    every forwarded header after resolving it so other middleware cannot apply
    a different interpretation; Host remains Django's separately checked input.
    """
    peer = ip_address(request.META.get("REMOTE_ADDR", ""))
    request.client_address = peer
    forwarded = any(key in request.META for key in FORWARDED)
    trusted = settings.STEWARDSHIP_PROXY_HOPS == 1 and _in_networks(
        peer, settings.STEWARDSHIP_TRUSTED_PROXY_NETWORKS
    )
    if trusted and forwarded:
        if "HTTP_FORWARDED" in request.META:
            raise ValueError("Unsupported forwarding format.")
        candidate = request.META.get("HTTP_X_FORWARDED_FOR", "")
        scheme = request.META.get("HTTP_X_FORWARDED_PROTO", "")
        if scheme not in {"http", "https"} or len(candidate) > 45:
            raise ValueError("Invalid forwarding metadata.")
        request.client_address = ip_address(candidate)
        request.META["wsgi.url_scheme"] = scheme
    for key in FORWARDED:
        request.META.pop(key, None)
    request.internal_request = not forwarded and _in_networks(
        peer, settings.STEWARDSHIP_INTERNAL_NETWORKS
    )


class SecurityBoundaryMiddleware:
    """Run before sessions/CSRF: validate ingress, hide internal routes, seal errors."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        """Apply a single security envelope even to errors and early denials."""
        try:
            request.get_host()
            resolve_client(request)
        except (ValueError, DisallowedHost):
            response = error_response(request, status=400)
        else:
            if (
                request.path_info in {"/health/live", "/health/ready", "/metrics"}
                and not request.internal_request
            ):
                response = error_response(request, status=404)
            else:
                response = self.get_response(request)
        # Django's DEBUG handlers may include tokens/paths or exception values.
        # Preserve typed validation 400/403 responses supplied by our views only
        # when explicitly marked; otherwise normalize framework error responses.
        if response.status_code in {400, 403, 404, 500} and not getattr(
            response, "stewardship_safe_error", False
        ):
            status = response.status_code
            response.close()
            response = error_response(request, status=status)
        response["Content-Security-Policy"] = CSP
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"] = "DENY"
        response["Referrer-Policy"] = "no-referrer"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.is_secure() and settings.SECURE_HSTS_SECONDS:
            response["Strict-Transport-Security"] = (
                f"max-age={settings.SECURE_HSTS_SECONDS}"
            )
        if not request.path_info.startswith(settings.STATIC_URL):
            response["Cache-Control"] = "no-store"
        return response
