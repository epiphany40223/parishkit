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


def render_caddy(configuration):
    """Keep private data out of access and error logs, including upstream failures.

    Dropping the request object also handles encoded token paths without trying
    to enumerate every URL representation. Operational logs retain level, status,
    byte counts and duration; sensitive free-form errors become a fixed message.
    No active health checks remove the app when business readiness is unavailable.
    """
    hostname = production_hostname(configuration)
    budget = configuration.runtime_budget
    upstreams = " ".join(
        configuration.runtime_network.web(index) + ":8000"
        for index in range(budget.replicas)
    )
    return f"""{{
    admin off
    http_port 8080
    https_port 8443
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

{hostname} {{
    tls {{
        issuer acme {{
            dir https://acme-v02.api.letsencrypt.org/directory
        }}
    }}
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
        request_body {{
            max_size 6MB
        }}
        handle_path /static/* {{
            root * /srv/static
            file_server
        }}
        reverse_proxy {upstreams} {{
            header_up -Forwarded
            header_up -X-Real-IP
            transport http {{
                dial_timeout 5s
                response_header_timeout {budget.proxy_timeout_seconds}s
                read_timeout {budget.proxy_timeout_seconds}s
                write_timeout {budget.proxy_timeout_seconds}s
            }}
        }}
    }}
}}
"""
