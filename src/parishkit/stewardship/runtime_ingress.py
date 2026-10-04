"""Stock Caddy configuration with bounded transport and private request logging."""

from ipaddress import ip_address
from urllib.parse import urlsplit

from parishkit.config import ConfigError

from .deployment import DeploymentProfile


def production_hostname(configuration):
    """Public ACME ingress requires a DNS hostname on standard HTTPS port 443."""
    origin = urlsplit(configuration.public_origin)
    if (
        configuration.profile is not DeploymentProfile.PRODUCTION
        or origin.scheme != "https"
        or origin.port not in (None, 443)
        or not origin.hostname
        or origin.hostname == "localhost"
        or "." not in origin.hostname
    ):
        raise ConfigError("Production ingress requires a public HTTPS DNS origin.")
    try:
        ip_address(origin.hostname)
    except ValueError:
        return origin.hostname
    raise ConfigError("Production ingress requires a public HTTPS DNS origin.")


# Served by Caddy itself when no web replica answers, above all during an
# upgrade, when web is stopped but Caddy keeps running (#162). It is fully
# self-contained: inline style attributes only (no <style> block, whose braces
# the Caddyfile placeholder syntax would claim), no scripts and no application
# assets, since the static tree may be mid-refresh. It reloads itself after a
# minute, and names no parish, so every deployment serves the same text.
MAINTENANCE_PAGE = "\n".join(
    (
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta http-equiv="refresh" content="60">',
        "<title>Updating the site</title>",
        "</head>",
        '<body style="margin:0;background:#f6f4ef;color:#1b2a31;'
        "font:18px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif\">",
        '<main style="max-width:34rem;margin:12vh auto;padding:2rem 1.5rem;'
        'background:#fff;border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.12)">',
        '<h1 style="margin:0 0 .75rem;font-size:1.5rem">'
        "We&rsquo;re updating the site</h1>",
        '<p style="margin:0 0 .75rem">Please try again in a couple of minutes. '
        "This page will reload by itself.</p>",
        '<p style="margin:0;color:#4f5e66">Thank you for your patience.</p>',
        "</main>",
        "</body>",
        "</html>",
    )
)

# Only for a site served while the application is unreachable: a strict
# policy (the page has no scripts or remote assets) and no caching, so a
# browser never keeps the maintenance page once the site is back.
_MAINTENANCE_HEADERS = (
    ("Content-Type", "text/html; charset=utf-8"),
    ("Cache-Control", "no-store"),
    ("Retry-After", "120"),
    (
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'",
    ),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
)


def _maintenance_block():
    """Caddy's error route for an unreachable application.

    ``reverse_proxy`` raises a handler error only for its own upstream
    failures: 502 for a refused or failed dial, and also for a connection
    reset mid-request (a worker killed or out of memory); 503 when no upstream
    is available. Those all show this page, recorded as 503 in the access log.
    A response the application itself returns, including its own 500s, passes
    straight through and never reaches ``handle_errors``, so the page cannot
    hide a real application error. Upstream timeouts (504) are deliberately
    left alone: a slow request is not an upgrade.
    """
    if "{" in MAINTENANCE_PAGE or "}" in MAINTENANCE_PAGE:
        raise ValueError("The maintenance page must not contain Caddy braces.")
    headers = "\n".join(
        f'            {name} "{value}"' for name, value in _MAINTENANCE_HEADERS
    )
    body = "\n".join("            " + line for line in MAINTENANCE_PAGE.split("\n"))
    return f"""    handle_errors 502 503 {{
        header {{
{headers}
        }}
        respond <<MAINTENANCE
{body}
            MAINTENANCE 503
    }}
"""


# The one route that admits a request body larger than 6 MB (#346).
HOSTED_FILE_UPLOAD = "/admin/files/upload"


def render_caddy(configuration):
    """The Production Caddyfile: a public ACME site on the standard ports.

    See ``_caddyfile`` for the shared body. Production listens on both the
    HTTP and HTTPS ports (HTTP redirects to HTTPS) and obtains its certificate
    from Let's Encrypt.
    """
    return _caddyfile(
        configuration,
        site=production_hostname(configuration),
        global_options="    http_port 8080\n    https_port 8443",
        tls="""tls {
        issuer acme {
            dir https://acme-v02.api.letsencrypt.org/directory
        }
    }""",
    )


def render_local_caddy(configuration):
    """The LOCAL Caddyfile (#476): ``localhost`` under Caddy's own local CA.

    The site is served with ``tls internal``, so there is no ACME account and
    no public certificate; ``skip_install_trust`` keeps Caddy from trying to
    install that CA into the container's trust store (the developer's
    ``ca`` command exports it instead), and ``auto_https disable_redirects``
    leaves Caddy with no HTTP listener at all, so only the HTTPS port exists
    to be published. Everything else (the maintenance page, log filters, body
    limits and timeouts) is the production body unchanged. This renderer never
    consults ``production_hostname``; the only admitted profile is LOCAL.
    """
    if configuration.profile is not DeploymentProfile.LOCAL:
        raise ConfigError("The local ingress serves only the local profile.")
    if urlsplit(configuration.public_origin).hostname != "localhost":
        raise ConfigError("The local ingress serves only localhost.")
    return _caddyfile(
        configuration,
        site="localhost",
        global_options="\n".join(
            (
                "    https_port 8443",
                "    auto_https disable_redirects",
                "    skip_install_trust",
            )
        ),
        tls="tls internal",
    )


def render_ingress(configuration):
    """The Caddyfile for a proxied profile: production's or LOCAL's.

    The topology renderer calls this for every ``behind_proxy`` profile, so
    the choice of ingress lives here beside the two renderers.
    """
    if configuration.profile is DeploymentProfile.LOCAL:
        return render_local_caddy(configuration)
    return render_caddy(configuration)


def _caddyfile(configuration, *, site, global_options, tls):
    """Keep private data out of access and error logs, including upstream failures.

    Dropping the request object also handles encoded token paths without trying
    to enumerate every URL representation. Operational logs retain level, status,
    byte counts and duration; sensitive free-form errors become a fixed message.
    No active health checks remove the app when business readiness is unavailable.
    When no web replica answers (an upgrade stops web while Caddy keeps
    running), Caddy serves its own self-contained maintenance page instead.
    Static files keep fixed names (``ui-v1.js``), so they are sent with
    ``Cache-Control: no-cache``: browsers revalidate each use against the
    file server's ETag, and a mid-campaign fix reaches returning Families.
    Hosted files (#346): only their upload route admits an 11 MB body (a
    10 MB file plus form overhead), and Caddy buffers each served file so a
    slow phone download never holds a web thread.

    ``site`` is the site address, ``global_options`` the profile's lines of
    the global options block (ports, automatic HTTPS and trust, already
    indented), and ``tls`` the site's whole ``tls`` directive; the production
    and local renderers differ only there.
    """
    budget = configuration.runtime_budget
    upstreams = " ".join(
        configuration.runtime_network.web(index) + ":8000"
        for index in range(budget.replicas)
    )
    transport = f"""            header_up -Forwarded
            header_up -X-Real-IP
            transport http {{
                dial_timeout 5s
                response_header_timeout {budget.proxy_timeout_seconds}s
                read_timeout {budget.proxy_timeout_seconds}s
                write_timeout {budget.proxy_timeout_seconds}s
            }}"""
    return f"""{{
    admin off
{global_options}
    log default {{
        output stdout
        format filter {{
            request delete
            resp_headers delete
            headers delete
            error delete
            msg replace proxy_event
            wrap json
        }}
    }}
    servers {{
        protocols h1 h2
        max_header_size 32KB
        timeouts {{
            read_header 10s
            read_body 30s
            write {budget.proxy_timeout_seconds}s
            idle 60s
        }}
    }}
}}

{site} {{
    {tls}
    log {{
        output stdout
        format filter {{
            request delete
            resp_headers delete
            headers delete
            error delete
            msg replace proxy_request
            wrap json
        }}
    }}
    route {{
        @internal path /health/* /metrics /metrics/*
        respond @internal 404
        @hosted_file_upload path {HOSTED_FILE_UPLOAD}
        request_body @hosted_file_upload {{
            max_size 11MB
        }}
        @ordinary_body not path {HOSTED_FILE_UPLOAD}
        request_body @ordinary_body {{
            max_size 6MB
        }}
        handle_path /static/* {{
            header Cache-Control "no-cache"
            root * /srv/static
            file_server
        }}
        @hosted_file path /files/*
        reverse_proxy @hosted_file {upstreams} {{
            response_buffers 11MB
{transport}
        }}
        reverse_proxy {upstreams} {{
{transport}
        }}
    }}
{_maintenance_block()}}}
"""
