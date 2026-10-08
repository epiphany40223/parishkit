"""The rendered LOCAL ingress and topology (#476, OPS-10.03, OPS-10.05).

Golden files pin the LOCAL Compose document and Caddyfile, and each property
the local environment specification's "topology and web differences" section
names is asserted directly: loopback-only publication, no application-egress,
an ingress network without IP masquerade joined only by Caddy and Mailpit,
``tls internal`` on ``localhost``, the local image tag with no GHCR reference,
the ``parishkit-local`` project name, the Mailpit mail catcher with its UI on
``127.0.0.1:8025`` only, the fake ParishSoft on ``backend`` alone, and the
LOCAL banner shown only in LOCAL.
"""

import hashlib
import inspect
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from parishkit.config import ConfigError
from parishkit.parishsoft_http_worker import LOCAL_SOURCE_BASE_URL
from parishkit.stewardship import runtime_provisioning as provisioning
from parishkit.stewardship.accounts import branding_context
from parishkit.stewardship.deployment import (
    LOCAL_PUBLIC_ORIGIN,
    DeploymentProfile,
    ServiceRole,
    load_deployment,
)
from parishkit.stewardship.jobs.queues import ROLE_QUEUES
from parishkit.stewardship.mail_catcher import LOCAL_SMTP_ENDPOINT
from parishkit.stewardship.runtime_database import offline_grants
from parishkit.stewardship.runtime_grants import runtime_grants
from parishkit.stewardship.runtime_identities import database_identities
from parishkit.stewardship.runtime_ingress import (
    HOSTED_FILE_UPLOAD,
    MAINTENANCE_PAGE,
    render_caddy,
    render_local_caddy,
)
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.runtime_topology import (
    DEVELOPMENT_IMAGE,
    MAILPIT_IMAGE,
    MAILPIT_MAX_MESSAGES,
    fake_parishsoft_configuration,
    mailpit_store,
    render_runtime,
)
from parishkit.stewardship.runtime_valkey import broker_acl, server_acl, web_acl

from .test_local_profile import (
    FIXTURES,
    GOLDEN_ROOT,
    LOCAL_IMAGE,
    PRODUCTION_IMAGE,
    PROFILES,
    canonical,
    configuration_for,
)

PROVIDER_MODES = ("initial", "configured", "configured-slack")
IMAGES = {
    DeploymentProfile.PRODUCTION: PRODUCTION_IMAGE,
    DeploymentProfile.LOCAL: LOCAL_IMAGE,
}

# The services on the non-internal ingress network: Caddy and the mail
# catcher's UI (OPS-10.05). The fake ParishSoft (OPS-10.06) joins backend only.
INGRESS_SERVICES = {"caddy", "mailpit"}
# The stock images LOCAL runs beside the application containers.
STOCK_SERVICES = {"postgres", "valkey", "caddy", "mailpit"}


def local_rendering(root, **options):
    """The LOCAL Compose document, its service documents and the Caddyfile path."""
    configuration = configuration_for(DeploymentProfile.LOCAL, root)
    compose, documents = render_runtime(configuration, image=LOCAL_IMAGE, **options)
    caddyfile = RuntimeLayout(configuration).service_directory / "Caddyfile"
    return configuration, compose, documents, caddyfile


def test_local_rendering_matches_the_golden_files():
    """Canonical-JSON identical to the committed fixtures.

    Regenerate both fixtures only for an intentional LOCAL change, from the
    repository root, with ``PYTHONPATH=src:tests``::

        from pathlib import Path
        from stewardship.test_local_profile import (
            FIXTURES, GOLDEN_ROOT, LOCAL_IMAGE, canonical, configuration_for)
        from parishkit.stewardship.deployment import DeploymentProfile
        from parishkit.stewardship.runtime_ingress import render_local_caddy
        from parishkit.stewardship.runtime_topology import render_runtime
        c = configuration_for(DeploymentProfile.LOCAL, GOLDEN_ROOT)
        compose, _ = render_runtime(c, image=LOCAL_IMAGE)
        (FIXTURES / "local-compose.golden.json").write_text(canonical(compose))
        (FIXTURES / "local-Caddyfile.golden").write_text(render_local_caddy(c))

    Then read the diff of both fixtures before committing: every changed line
    must be the intended LOCAL change, and the Production fixtures must not
    have changed at all.
    """
    configuration, compose, documents, caddyfile = local_rendering(GOLDEN_ROOT)
    assert documents[caddyfile] == render_local_caddy(configuration)
    golden = (FIXTURES / "local-compose.golden.json").read_text()
    assert canonical(compose) == golden
    golden = (FIXTURES / "local-Caddyfile.golden").read_text()
    assert render_local_caddy(configuration) == golden


@pytest.mark.parametrize("mode", PROVIDER_MODES)
def test_local_topology_has_no_egress_and_a_loopback_only_ingress(tmp_path, mode):
    """No application-egress at all; ingress without masquerade, Caddy and Mailpit.

    In every provider mount mode: the egress joins are per service and mode
    independent, but a mode must not be able to bring one back.
    """
    configuration, compose, _, _ = local_rendering(tmp_path, provider_mode=mode)
    assert compose["name"] == "parishkit-local"
    networks = compose["networks"]
    assert set(networks) == {"backend", "proxy", "ingress"}
    assert networks["backend"]["internal"] is True
    assert networks["proxy"]["internal"] is True
    assert networks["ingress"] == {
        "driver_opts": {"com.docker.network.bridge.enable_ip_masquerade": "false"}
    }
    services = compose["services"]
    on_ingress = {name for name, s in services.items() if "ingress" in s["networks"]}
    assert on_ingress == INGRESS_SERVICES
    for name, service in services.items():
        assert "application-egress" not in service["networks"], name
        for port in service.get("ports", []):
            assert port.startswith("127.0.0.1:"), (name, port)
            assert "0.0.0.0" not in port
    assert services["caddy"]["ports"] == ["127.0.0.1:8443:8443"]
    assert services["caddy"]["healthcheck"]["test"] == [
        "CMD-SHELL",
        "nc -z -w 2 127.0.0.1 8443",
    ]
    assert set(services["caddy"]["networks"]) == {"proxy", "ingress"}
    assert services["caddy"]["networks"]["proxy"] == {
        "ipv4_address": configuration.runtime_network.caddy
    }
    # Only Caddy and the mail catcher's UI publish anything, each on the VM's
    # loopback; web is reached through the proxy network.
    assert {name for name, s in services.items() if "ports" in s} == INGRESS_SERVICES
    assert services["mailpit"]["ports"] == ["127.0.0.1:8025:8025"]


def test_local_application_services_use_the_local_tag_and_no_registry(tmp_path):
    """Every application container runs the local build; nothing names GHCR."""
    _, compose, documents, caddyfile = local_rendering(tmp_path)
    for name, service in compose["services"].items():
        if name not in STOCK_SERVICES:
            assert service["image"] == LOCAL_IMAGE, name
        assert "ghcr.io" not in service["image"], name
    assert "ghcr.io" not in canonical(compose)
    assert "ghcr.io" not in documents[caddyfile]


# The mail catcher (OPS-10.05).
@pytest.mark.parametrize("mode", PROVIDER_MODES)
def test_mailpit_receives_on_backend_and_publishes_its_ui_on_loopback_only(
    tmp_path, mode
):
    """Mailpit: pinned digest, backend and ingress, one loopback port, no relay.

    The application reaches it at ``LOCAL_SMTP_ENDPOINT`` over the backend
    network; its UI is published on the VM's loopback alone. No relay setting
    is rendered, so Mailpit keeps every message it receives, and its Host
    allowlist stops a page in the developer's browser from reading them.
    """
    configuration, compose, _, _ = local_rendering(tmp_path, provider_mode=mode)
    mailpit = compose["services"]["mailpit"]
    assert mailpit["image"] == MAILPIT_IMAGE
    assert mailpit["image"].startswith("axllent/mailpit:v")
    assert "@sha256:" in mailpit["image"] and "ghcr.io" not in mailpit["image"]
    assert set(mailpit["networks"]) == {"backend", "ingress"}
    assert mailpit["ports"] == ["127.0.0.1:8025:8025"]
    host, port = LOCAL_SMTP_ENDPOINT
    assert host == "mailpit"
    environment = mailpit["environment"]
    assert environment["MP_SMTP_BIND_ADDR"] == f"0.0.0.0:{port}"
    assert environment["MP_UI_BIND_ADDR"] == "0.0.0.0:8025"
    assert environment["MP_DATABASE"] == "/data/mailpit.db"
    assert environment["MP_MAX_MESSAGES"] == str(MAILPIT_MAX_MESSAGES) == "50000"
    assert environment["MP_ALLOWED_HOSTS"] == "localhost,127.0.0.1"
    # Mailpit can only send mail onward through a relay, a forward, a webhook
    # or POP3 release; none of those is rendered, and relaying is off by name.
    assert environment["MP_SMTP_RELAY_ALL"] == "false"
    for name in environment:
        assert not (name.startswith("MP_SMTP_RELAY") and name != "MP_SMTP_RELAY_ALL")
        assert not name.startswith("MP_SMTP_FORWARD")
        assert name not in {"MP_WEBHOOK_URL", "MP_POP3_AUTH_FILE"}
    assert mailpit["stop_grace_period"] == "10s"
    # Run like the other stock images: unprivileged, read-only, one store.
    assert mailpit["user"] == "10001:10001"
    assert mailpit["read_only"] is True and mailpit["cap_drop"] == ["ALL"]
    assert mailpit["restart"] == "unless-stopped"
    assert mailpit["healthcheck"]["test"] == ["CMD", "/mailpit", "readyz"]
    assert mailpit["volumes"] == [
        {
            "type": "bind",
            "source": str(mailpit_store(configuration)),
            "target": "/data",
            "read_only": False,
            "bind": {"create_host_path": False},
        }
    ]
    assert mailpit_store(configuration) == (
        configuration.paths["persistent_root"] / "mailpit"
    )
    assert "command" not in mailpit and "entrypoint" not in mailpit


@pytest.mark.parametrize(
    "profile",
    [profile for profile in PROFILES if profile is not DeploymentProfile.LOCAL],
)
def test_no_other_profile_renders_the_local_services(profile):
    """Development, test and Production have no Mailpit or fake ParishSoft."""
    configuration = configuration_for(profile, GOLDEN_ROOT)
    compose, _ = render_runtime(
        configuration, image=IMAGES.get(profile, DEVELOPMENT_IMAGE)
    )
    assert "mailpit" not in compose["services"]
    assert "fake-parishsoft" not in compose["services"]
    rendered = canonical(compose)
    assert "mailpit" not in rendered and "axllent" not in rendered
    assert MAILPIT_IMAGE not in rendered
    assert "fake-parishsoft" not in rendered and "fake_parishsoft" not in rendered


# The fake ParishSoft service (OPS-10.05, command from OPS-10.06).
@pytest.mark.parametrize("mode", PROVIDER_MODES)
def test_fake_parishsoft_serves_backend_only_from_its_one_mount(tmp_path, mode):
    """The fake: local image, backend only, no port, one read-only config mount.

    It answers at ``LOCAL_SOURCE_BASE_URL``, so its service name and port are
    that URL's; the worker and the ParishSoft credential installer, which
    call ParishSoft, share the ``backend`` network with it.
    """
    configuration, compose, _, _ = local_rendering(tmp_path, provider_mode=mode)
    services = compose["services"]
    fake = services["fake-parishsoft"]
    target = urlsplit(LOCAL_SOURCE_BASE_URL)
    assert target.hostname == "fake-parishsoft" and target.port == 8080
    assert fake["image"] == LOCAL_IMAGE
    assert set(fake["networks"]) == {"backend"}
    assert "ports" not in fake
    for name in ("worker", "credential-installer-parishsoft"):
        assert "backend" in services[name]["networks"]
    path = fake_parishsoft_configuration(configuration)
    assert path == configuration.paths["run"] / "local" / "fake-parishsoft.json"
    assert fake["command"] == [
        "fake-parishsoft",
        "--profile",
        "local",
        "--fake-config",
        str(path),
        "--port",
        "8080",
    ]
    assert fake["volumes"] == [
        {
            "type": "bind",
            "source": str(path),
            "target": str(path),
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    ]
    assert fake["user"] == "10001:10001"
    assert fake["read_only"] is True and fake["cap_drop"] == ["ALL"]
    assert fake["restart"] == "unless-stopped"
    assert fake["healthcheck"]["test"][:3] == ["CMD", "python", "-c"]
    assert "8080" in fake["healthcheck"]["test"][3]
    # No credential of any kind reaches the fake.
    assert not any("credential" in mount["source"] for mount in fake["volumes"])
    assert set(fake["environment"]) == set(services["scheduler"]["environment"])


@pytest.mark.parametrize(
    "profile", [DeploymentProfile.LOCAL, DeploymentProfile.PRODUCTION]
)
def test_only_local_provisions_the_mail_catcher_store(tmp_path, profile):
    """Provisioning creates ``run/persistent/mailpit`` for LOCAL and nothing else."""
    root = tmp_path / "runtime"
    configuration = configuration_for(profile, root)
    result = provisioning.provision_runtime(configuration, image=IMAGES[profile])
    assert result["runtime_storage_provisioned"] is True
    store = root / "run" / "persistent" / "mailpit"
    assert store == mailpit_store(configuration)
    if profile is DeploymentProfile.LOCAL:
        assert store.is_dir() and (store.stat().st_mode & 0o777) == 0o700
    else:
        assert not store.exists()


def test_local_caddyfile_serves_localhost_with_an_internal_certificate(tmp_path):
    """tls internal, no ACME, no HTTP listener, no trust-store install."""
    configuration, _, documents, caddyfile = local_rendering(tmp_path)
    rendered = documents[caddyfile]
    assert "\nlocalhost {\n    tls internal\n" in rendered
    assert "acme" not in rendered
    assert "letsencrypt" not in rendered
    assert "http_port" not in rendered
    assert "    auto_https disable_redirects\n" in rendered
    assert "    skip_install_trust\n" in rendered
    assert "    https_port 8443\n" in rendered
    assert "    admin off\n" in rendered
    assert configuration.runtime_network.web(0) + ":8000" in rendered
    assert "parish.example" not in rendered


def test_local_caddyfile_keeps_the_production_body(tmp_path):
    """Maintenance page, log filters, body limits and transport are production's.

    The two Caddyfiles differ only in the global options' port and
    automatic-HTTPS lines, the site address and the ``tls`` directive. The
    rest of the global block (from ``log default`` to the site address) and
    everything from the site's ``log`` block on are byte-identical.
    """
    local = configuration_for(DeploymentProfile.LOCAL, tmp_path)
    production = configuration_for(DeploymentProfile.PRODUCTION, tmp_path)
    local_text, production_text = render_local_caddy(local), render_caddy(production)

    def global_block(text, site):
        start = text.index("    log default {\n")
        return text[start : text.index(f"\n\n{site} {{\n")]

    assert global_block(local_text, "localhost") == global_block(
        production_text, "parish.example"
    )
    marker = "    log {\n"
    assert (
        local_text[local_text.index(marker) :]
        == (production_text[production_text.index(marker) :])
    )
    for text in (local_text, production_text):
        assert MAINTENANCE_PAGE.replace("\n", "\n            ") in text
        assert HOSTED_FILE_UPLOAD in text
        assert "request delete" in text
        assert "max_size 11MB" in text and "max_size 6MB" in text


@pytest.mark.parametrize("profile", PROFILES)
def test_local_caddyfile_renders_only_the_local_profile(profile):
    """The local renderer refuses every other profile, as production's does LOCAL."""
    configuration = configuration_for(profile)
    if profile is DeploymentProfile.LOCAL:
        assert render_local_caddy(configuration).startswith("{\n    admin off\n")
    else:
        with pytest.raises(ConfigError, match="only the local profile"):
            render_local_caddy(configuration)


def test_local_service_documents_reload_as_local(tmp_path):
    """Each rendered service document carries the LOCAL profile and origin."""
    _, _, documents, caddyfile = local_rendering(tmp_path)
    documents.pop(caddyfile)
    assert documents
    for path, document in documents.items():
        assert path.suffix == ".yaml"
        loaded = load_deployment(document=document, environ={})
        assert loaded.profile is DeploymentProfile.LOCAL
        assert loaded.public_origin == LOCAL_PUBLIC_ORIGIN
        assert loaded.trusted_proxy_hops == 1


def _normalized(value):
    """Sets become sorted lists so a digest does not depend on the hash seed."""
    if isinstance(value, (set, frozenset)):
        return sorted(_normalized(item) for item in value)
    if isinstance(value, dict):
        return {str(key): _normalized(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    if isinstance(value, bytes):
        return value.decode()
    return value if isinstance(value, (str, int, bool)) or value is None else str(value)


def grants_and_acls():
    """One digest of every SQL grant map and Valkey ACL the runtime renders."""
    grants = {}
    for name, _, role, target in database_identities():
        if role is not ServiceRole.MIGRATION:
            grants[name] = runtime_grants(role, target=target)
    for role in (ServiceRole.BOOTSTRAP, ServiceRole.ADMIN_RECOVERY):
        grants["offline:" + role.value] = offline_grants(role)
    passwords = {ServiceRole.WEB: b"web-pw", **{r: b"q-pw" for r in ROLE_QUEUES}}
    acls = {
        "web": web_acl(b"web-pw"),
        **{"broker:" + r.value: broker_acl(r, b"q-pw") for r in ROLE_QUEUES},
        "server": server_acl(passwords),
    }
    return hashlib.sha256(
        json.dumps(_normalized({"grants": grants, "acls": acls})).encode()
    ).hexdigest()


def rendered_identities(profile):
    """Each service's SQL login and password files and Valkey credential file."""
    configuration = configuration_for(profile, GOLDEN_ROOT)
    _, documents = render_runtime(
        configuration, image=IMAGES.get(profile, DEVELOPMENT_IMAGE)
    )
    result = {}
    for path, document in documents.items():
        if path.name == "Caddyfile":
            continue
        loaded = load_deployment(document=document, environ={})
        result[path.name] = (
            loaded.postgres.user,
            str(loaded.postgres.password_file),
            str(loaded.postgres.download_password_file),
            str(loaded.valkey.password_file),
        )
    return result


@pytest.mark.parametrize("profile", PROFILES)
def test_grants_logins_and_valkey_acls_are_identical_across_profiles(profile):
    """LOCAL adds no login, role, grant or ACL: every profile renders the same.

    The grant and ACL renderers take a role (and a password), never a
    configuration or profile, so they cannot vary by profile; the signatures
    assert that stays so. The rendered service documents then prove that each
    profile's services use the same SQL logins and credential files as
    production, and the grants digest is the same under every profile.
    """
    for function in (runtime_grants, offline_grants, web_acl, broker_acl, server_acl):
        parameters = set(inspect.signature(function).parameters)
        assert not parameters & {"configuration", "profile", "deployment"}, function
    assert grants_and_acls() == grants_and_acls()
    baseline = rendered_identities(DeploymentProfile.PRODUCTION)
    assert set(baseline) >= {"web.yaml", "worker.yaml", "mail-dispatch.yaml"}
    assert rendered_identities(profile) == baseline


# The LOCAL banner.
@pytest.mark.parametrize("profile", [None, *PROFILES])
def test_banner_flag_is_set_only_in_local(settings, profile):
    """The context processor flags LOCAL alone; an unset profile shows nothing."""
    if profile is None:
        settings.STEWARDSHIP_DEPLOYMENT_PROFILE = None
    else:
        settings.STEWARDSHIP_DEPLOYMENT_PROFILE = profile.value
    context = branding_context.local_environment(RequestFactory().get("/"))
    if profile is DeploymentProfile.LOCAL:
        assert context == {"local_environment": True}
    else:
        assert context == {}


def test_banner_is_registered_and_rendered_only_when_flagged(settings):
    """base.html shows the LOCAL banner from the flag, and never without it."""
    processors = settings.TEMPLATES[0]["OPTIONS"]["context_processors"]
    assert "parishkit.stewardship.accounts.branding_context.local_environment" in (
        processors
    )
    shown = render_to_string("stewardship/base.html", {"local_environment": True})
    assert 'class="local-environment" role="note" aria-label="Local environment"' in (
        shown
    )
    assert "<strong>LOCAL</strong>" in shown
    assert "local laptop environment" in shown
    # The banner precedes the site header, so it is the first thing on the page.
    assert shown.index("local-environment") < shown.index("site-header")
    hidden = render_to_string("stewardship/base.html", {})
    assert "local-environment" not in hidden
    assert 'role="note"' not in hidden


def test_banner_style_does_not_rely_on_color_alone():
    """The stylesheet gives the banner a text label and a hatched edge."""
    css = (
        Path(branding_context.__file__).parent / "static" / "stewardship" / "ui-v1.css"
    ).read_text()
    assert ".local-environment {" in css
    assert (
        "repeating-linear-gradient"
        in css.split(".local-environment {")[1].split("\n")[0]
    )
