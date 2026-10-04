"""The LOCAL profile (#476) never takes a development or test branch.

One test per row of the local environment specification's "existing profile
branches" table, each also asserting that every other profile's result is
unchanged, plus the Production golden files that pin the rendered Compose
document and Caddyfile byte for byte while local-environment work lands.
"""

import hashlib
import io
import json
import os
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import deployment as deployment_module
from parishkit.stewardship import runtime_process, services
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import (
    LOCAL_PUBLIC_ORIGIN,
    DeploymentProfile,
    ServiceRole,
    load_deployment,
)
from parishkit.stewardship.deployment_documents import deployment_document
from parishkit.stewardship.runtime_ingress import (
    production_hostname,
    render_caddy,
    render_local_caddy,
)
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.runtime_topology import (
    CADDY_IMAGE,
    DEVELOPMENT_IMAGE,
    _image,
    render_runtime,
)
from parishkit.stewardship.runtime_web import template_settings, trusted_proxy_networks
from parishkit.stewardship.service_boundaries import Mount, validate_mounts
from parishkit.stewardship.web.security import browser_settings

from .test_deployment import config_file
from .test_runtime_topology import IMAGE as PRODUCTION_IMAGE
from .test_service_boundaries import configured

FIXTURES = Path(__file__).with_name("fixtures")
PROFILES = list(DeploymentProfile)
# The two profiles whose web is reached directly, with no proxy.
DIRECT = (DeploymentProfile.DEVELOPMENT, DeploymentProfile.TEST)
PROXIED = (DeploymentProfile.PRODUCTION, DeploymentProfile.LOCAL)
COMMIT = "0123456789abcdef0123456789abcdef01234567"
LOCAL_IMAGE = f"parishkit-stewardship-local:{COMMIT}-1700000000"
LOCAL_DIRTY_IMAGE = f"parishkit-stewardship-local:{COMMIT}-dirty-1700000000"
# Production and LOCAL render at this fixed root so the golden files (theirs
# and test_local_topology's) hold no test path.
GOLDEN_ROOT = Path("/opt/parishkit")
# SHA-256 of the canonical JSON (see ``canonical``) of each Production
# rendering on main at cbea35b2 (2026-10-03), before the LOCAL profile existed.
# The "configured" Compose document and the Caddyfile are also committed in
# full under fixtures/ so a difference can be read, not just detected.
GOLDEN_DIGESTS = {
    ("initial", "compose"): (
        "14ca48e3d1a8390e697abd5011c4ab896127809bfbcb72c0daa323242f2ac968"
    ),
    ("initial", "documents"): (
        "7e34be432d98fe26781a67030ffb76d1f0ed85f850bb072bad74b4a8c9dfdeba"
    ),
    ("configured", "compose"): (
        "0e132d5c1ccfe4755a1f63862ba495e4d9d9ab061084f572e65ad572b0ba184c"
    ),
    ("configured", "documents"): (
        "9d65bab1d7b28eebd476ee671871d675f86ff81cff6259cd82ded7b936a1d360"
    ),
    ("configured-slack", "compose"): (
        "737a22ecbf75274a9005583a08379ab84ecaafd27e2c0dab5e20a82b264d2da4"
    ),
    ("configured-slack", "documents"): (
        "81fa8932b36a6d3a9e41c0301d63dc9ab30501f950cf815bf3e8b2caf2dc8b6c"
    ),
}


def configuration_for(profile, root="/opt/parishkit"):
    """A web deployment of the given profile, resolved without touching disk."""
    origins = {
        DeploymentProfile.PRODUCTION: "https://parish.example",
        DeploymentProfile.LOCAL: LOCAL_PUBLIC_ORIGIN,
    }
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(root)})
    return replace(
        configuration,
        profile=profile,
        public_origin=origins.get(profile, "http://localhost:8010"),
        trusted_proxy_hops=int(profile.behind_proxy),
        valkey=replace(
            configuration.valkey, password_file=Path(root) / "broker-password"
        ),
    )


def canonical(value):
    """The one JSON form the golden files and digests are computed from.

    Keys are sorted, and so is each service's ``volumes`` list: the renderer
    builds a service's mounts from sets, so on main their order already
    differs from one Python process to the next (hash randomization; #480).
    Nothing else is normalized.
    """
    value = json.loads(json.dumps(value))
    for service in value.get("services", {}).values():
        if "volumes" in service:
            service["volumes"].sort(key=lambda m: (m["target"], m["source"]))
    return json.dumps(value, indent=1, sort_keys=True) + "\n"


def test_local_is_a_fourth_profile_behind_a_proxy():
    """LOCAL parses as "local"; behind_proxy is true for exactly the proxied two."""
    assert DeploymentProfile("local") is DeploymentProfile.LOCAL
    assert set(PROFILES) == {*DIRECT, *PROXIED}
    for profile in PROXIED:
        assert profile.behind_proxy is True
    for profile in DIRECT:
        assert profile.behind_proxy is False
    assert LOCAL_PUBLIC_ORIGIN == "https://localhost:8443"


# Row: deployment._origin and the default origin.
@pytest.mark.parametrize(
    "origin",
    [None, LOCAL_PUBLIC_ORIGIN, "https://localhost:8443/", "https://LOCALHOST:8443"],
)
def test_local_origin_defaults_to_and_accepts_only_its_one_value(tmp_path, origin):
    """Absent, exact, trailing-slash and upper-case forms all mean the one origin."""
    document = {"profile": "local"}
    if origin is not None:
        document["public_origin"] = origin
    configuration = load_deployment(config_file(tmp_path, document), environ={})
    assert configuration.profile is DeploymentProfile.LOCAL
    assert configuration.public_origin == LOCAL_PUBLIC_ORIGIN
    assert configuration.trusted_proxy_hops == 1
    by_environment = load_deployment(environ={"PARISHKIT_STEWARDSHIP_PROFILE": "local"})
    assert by_environment.public_origin == LOCAL_PUBLIC_ORIGIN
    assert by_environment.trusted_proxy_hops == 1


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8443",
        "http://localhost:8000",
        "https://localhost",
        "https://localhost:443",
        "https://localhost:8000",
        "https://localhost:8444",
        "https://127.0.0.1:8443",
        "https://[::1]:8443",
        "https://localhost.localdomain:8443",
        "https://parish.example",
        "https://parish.example:8443",
        "https://localhost:8443/path",
        "https://localhost:8443?query",
        "https://user:synthetic-secret@localhost:8443",
    ],
)
def test_local_refuses_every_other_origin(tmp_path, origin):
    """Any other scheme, host (loopback addresses included) or port is refused."""
    path = config_file(tmp_path, {"profile": "local", "public_origin": origin})
    with pytest.raises(ConfigError) as error:
        load_deployment(path, environ={})
    assert "synthetic-secret" not in str(error.value)


@pytest.mark.parametrize("profile", DIRECT)
def test_development_and_test_origins_are_unchanged(tmp_path, profile):
    """The loopback HTTP default and rule are untouched, and LOCAL's origin fails."""
    configuration = load_deployment(
        config_file(tmp_path, {"profile": profile.value}), environ={}
    )
    assert configuration.public_origin == "http://localhost:8000"
    assert configuration.trusted_proxy_hops == 0
    accepted = load_deployment(
        config_file(
            tmp_path, {"profile": profile.value, "public_origin": "http://[::1]:8001"}
        ),
        environ={},
    )
    assert accepted.public_origin == "http://[::1]:8001"
    for origin in (LOCAL_PUBLIC_ORIGIN, "https://localhost", "http://parish.example"):
        with pytest.raises(ConfigError, match="development/test"):
            load_deployment(
                config_file(
                    tmp_path, {"profile": profile.value, "public_origin": origin}
                ),
                environ={},
            )


def test_production_origin_rule_is_unchanged(tmp_path):
    """No implicit origin, HTTPS required, one hop."""
    with pytest.raises(ConfigError):
        load_deployment(config_file(tmp_path, {"profile": "production"}), environ={})
    with pytest.raises(ConfigError, match="requires HTTPS"):
        load_deployment(
            config_file(
                tmp_path,
                {"profile": "production", "public_origin": "http://parish.example"},
            ),
            environ={},
        )
    configuration = load_deployment(
        config_file(
            tmp_path,
            {"profile": "production", "public_origin": "https://parish.example"},
        ),
        environ={},
    )
    assert configuration.trusted_proxy_hops == 1


# Row: proxy hops.
@pytest.mark.parametrize("profile", PROFILES)
def test_proxy_hops_follow_behind_proxy_and_name_the_direct_profiles(tmp_path, profile):
    """One hop behind Caddy, none otherwise; the refusal no longer says "locally"."""
    expected = 1 if profile.behind_proxy else 0
    document = {"profile": profile.value}
    if profile is DeploymentProfile.PRODUCTION:
        document["public_origin"] = "https://parish.example"
    assert (
        load_deployment(config_file(tmp_path, document), environ={}).trusted_proxy_hops
        == expected
    )
    with pytest.raises(ConfigError) as error:
        load_deployment(
            config_file(tmp_path, {**document, "trusted_proxy_hops": 1 - expected}),
            environ={},
        )
    message = str(error.value)
    assert "development and test" in message
    assert "locally" not in message


# Row: the authentication-limit warning.
@pytest.mark.parametrize("profile", PROFILES)
def test_weaker_authentication_limits_warn_only_in_production(
    tmp_path, monkeypatch, profile
):
    """LOCAL holds synthetic data, so it is not warned; production still is."""
    warned = Mock(return_value=("admin_burst",))
    monkeypatch.setattr(
        deployment_module.AuthenticationLimits, "warn_if_weaker", warned
    )
    document = {"profile": profile.value, "authentication_limits": {"admin_burst": 900}}
    if profile is DeploymentProfile.PRODUCTION:
        document["public_origin"] = "https://parish.example"
    load_deployment(config_file(tmp_path, document), environ={})
    assert warned.call_count == (1 if profile is DeploymentProfile.PRODUCTION else 0)


# Row: runtime_topology._image.
@pytest.mark.parametrize("image", [LOCAL_IMAGE, LOCAL_DIRTY_IMAGE])
def test_local_admits_only_the_local_build_tag(image):
    """A commit, an optional -dirty marker and a build time, nothing else."""
    assert _image(image, DeploymentProfile.LOCAL) == image
    for profile in (*DIRECT, DeploymentProfile.PRODUCTION):
        with pytest.raises(ConfigError, match="approved immutable"):
            _image(image, profile)


@pytest.mark.parametrize(
    "image",
    [
        PRODUCTION_IMAGE,
        DEVELOPMENT_IMAGE,
        "parishkit-stewardship-local:latest",
        f"parishkit-stewardship-local:{COMMIT}",
        f"parishkit-stewardship-local:{COMMIT[:-1]}-1700000000",
        f"parishkit-stewardship-local:{COMMIT.upper()}-1700000000",
        f"parishkit-stewardship-local:{COMMIT}-170000000",
        f"parishkit-stewardship-local:{COMMIT}-17000000000",
        f"parishkit-stewardship-local:{COMMIT}-1700000000-dirty",
        f"parishkit-stewardship-local:{COMMIT}-clean-1700000000",
        f"parishkit-stewardship:{COMMIT}-1700000000",
        f"ghcr.io/example/parishkit/stewardship-local:{COMMIT}-1700000000",
        f"parishkit-stewardship-local:{COMMIT}-1700000000 ",
        None,
    ],
)
def test_local_refuses_registry_development_and_malformed_images(image):
    """GHCR references, the development tag and near-misses are all refused."""
    with pytest.raises(ConfigError, match="approved immutable"):
        _image(image, DeploymentProfile.LOCAL)


def test_production_and_direct_image_rules_are_unchanged():
    """Production still needs its GHCR digest; development and test their tag."""
    assert _image(PRODUCTION_IMAGE, DeploymentProfile.PRODUCTION) == PRODUCTION_IMAGE
    with pytest.raises(ConfigError):
        _image(DEVELOPMENT_IMAGE, DeploymentProfile.PRODUCTION)
    with pytest.raises(ConfigError):
        _image(
            "ghcr.io/example/parishkit/stewardship:latest", DeploymentProfile.PRODUCTION
        )
    for profile in DIRECT:
        assert _image(DEVELOPMENT_IMAGE, profile) == DEVELOPMENT_IMAGE
        with pytest.raises(ConfigError):
            _image(PRODUCTION_IMAGE, profile)


# Row: source mounts.
@pytest.mark.parametrize("profile", PROFILES)
def test_source_mounts_are_rendered_only_for_development(tmp_path, profile):
    """The existing ``is not DEVELOPMENT`` refusal still covers LOCAL (and test)."""
    images = {
        DeploymentProfile.PRODUCTION: PRODUCTION_IMAGE,
        DeploymentProfile.LOCAL: LOCAL_IMAGE,
    }
    configuration = configuration_for(profile, tmp_path)
    image = images.get(profile, DEVELOPMENT_IMAGE)
    if profile is DeploymentProfile.DEVELOPMENT:
        compose, _ = render_runtime(configuration, image=image, checkout=tmp_path)
        assert any(
            mount["target"] == "/app/src"
            for mount in compose["services"]["web"]["volumes"]
        )
    else:
        with pytest.raises(ConfigError, match="explicit development checkout"):
            render_runtime(configuration, image=image, checkout=tmp_path)


# Rows: web replica networking, and Caddy with its restart policy.
def test_local_web_joins_the_proxy_network_behind_caddy_like_production(tmp_path):
    """LOCAL never renders the development shape (published web port, no Caddy)."""
    configuration = configuration_for(DeploymentProfile.LOCAL, tmp_path)
    compose, documents = render_runtime(configuration, image=LOCAL_IMAGE)
    web = compose["services"]["web"]
    assert web["networks"]["proxy"] == {
        "ipv4_address": configuration.runtime_network.web(0)
    }
    assert "ports" not in web
    caddy = compose["services"]["caddy"]
    assert caddy["image"] == CADDY_IMAGE
    assert set(caddy["networks"]) == {"proxy", "ingress"}
    caddyfile = RuntimeLayout(configuration).service_directory / "Caddyfile"
    assert documents[caddyfile] == render_local_caddy(configuration)
    for name, service in compose["services"].items():
        expected = "no" if "profiles" in service else "unless-stopped"
        assert service["restart"] == expected, name


@pytest.mark.parametrize("profile", DIRECT)
def test_direct_profiles_still_render_a_published_web_port_and_no_caddy(
    tmp_path, profile
):
    """Development and test keep their loopback publication and restart policy."""
    compose, documents = render_runtime(
        configuration_for(profile, tmp_path), image=DEVELOPMENT_IMAGE
    )
    web = compose["services"]["web"]
    assert web["ports"] == ["127.0.0.1:8010:8000"]
    assert "proxy" not in web["networks"]
    assert "caddy" not in compose["services"]
    assert all(path.name != "Caddyfile" for path in documents)
    assert all(service["restart"] == "no" for service in compose["services"].values())


# Production invariant: the rendered Compose document and Caddyfile.
def test_production_rendering_matches_the_golden_files():
    """Canonical-JSON identical to main before LOCAL, in every provider mode.

    Byte-identical up to the volume order ``canonical`` sorts (#480).
    Regenerate the two fixture files only for an intentional Production change,
    from a rendering at GOLDEN_ROOT with ``canonical``, and update the digests.
    """
    configuration = configuration_for(DeploymentProfile.PRODUCTION, GOLDEN_ROOT)
    caddy_path = RuntimeLayout(configuration).service_directory / "Caddyfile"
    for mode in ("initial", "configured", "configured-slack"):
        compose, documents = render_runtime(
            configuration, image=PRODUCTION_IMAGE, provider_mode=mode
        )
        assert documents.pop(caddy_path) == render_caddy(configuration)
        documents = {str(path): document for path, document in documents.items()}
        for name, value in (("compose", compose), ("documents", documents)):
            digest = hashlib.sha256(canonical(value).encode()).hexdigest()
            assert digest == GOLDEN_DIGESTS[mode, name], (mode, name)
        if mode == "configured":
            golden = (FIXTURES / "production-compose.golden.json").read_text()
            assert canonical(compose) == golden
    golden = (FIXTURES / "production-Caddyfile.golden").read_text()
    assert render_caddy(configuration) == golden


# Row: trusted proxy networks.
@pytest.mark.parametrize("profile", PROFILES)
def test_only_proxied_profiles_trust_caddy_to_forward(profile):
    """Caddy's /32 behind a proxy; no trusted peer at all when reached directly."""
    configuration = configuration_for(profile)
    networks = trusted_proxy_networks(configuration)
    if profile.behind_proxy:
        assert networks == (configuration.runtime_network.caddy + "/32",)
    else:
        assert networks == ()


# Row: template reload.
@pytest.mark.parametrize("profile", PROFILES)
def test_only_development_reloads_templates(profile):
    """LOCAL keeps the cached loaders, as test and production do."""
    templates = [{"APP_DIRS": True, "OPTIONS": {}}]
    result = template_settings(templates, configuration_for(profile))
    if profile is DeploymentProfile.DEVELOPMENT:
        assert result is not templates
        assert result[0]["APP_DIRS"] is False
        assert result[0]["OPTIONS"]["loaders"][0].endswith("filesystem.Loader")
        assert templates == [{"APP_DIRS": True, "OPTIONS": {}}]
    else:
        assert result is templates
        assert templates == [{"APP_DIRS": True, "OPTIONS": {}}]


# Row: the web/security "production" flag.
@pytest.mark.parametrize("profile", PROFILES)
def test_browser_flags_follow_behind_proxy_but_hsts_stays_production_only(profile):
    """Secure cookies and the HTTPS redirect for both proxied profiles; HSTS once."""
    configuration = configuration_for(profile)
    values = browser_settings(configuration)
    proxied = profile.behind_proxy
    assert values["SESSION_COOKIE_SECURE"] is proxied
    assert values["CSRF_COOKIE_SECURE"] is proxied
    assert values["SECURE_SSL_REDIRECT"] is proxied
    assert values["SECURE_HSTS_SECONDS"] == (
        31536000 if profile is DeploymentProfile.PRODUCTION else 0
    )
    assert values["STEWARDSHIP_CANONICAL_HOST"] == (
        "localhost" if profile is not DeploymentProfile.PRODUCTION else "parish.example"
    )
    if proxied:
        with pytest.raises(ValueError):
            browser_settings(
                SimpleNamespace(profile=profile, public_origin="http://localhost:8443")
            )


# Row: runtime_ingress.production_hostname.
@pytest.mark.parametrize("profile", PROFILES)
def test_production_hostname_still_serves_only_production(profile):
    """LOCAL is refused like development and test; its renderer is separate."""
    configuration = configuration_for(profile)
    if profile is DeploymentProfile.PRODUCTION:
        assert production_hostname(configuration) == "parish.example"
    else:
        with pytest.raises(ConfigError, match="public HTTPS DNS origin"):
            production_hostname(configuration)


# Row: the /app/src mount exemption, online and (not in the table but the same
# exemption) in the offline and backup validators.
@pytest.mark.parametrize("profile", PROFILES)
def test_source_mount_is_admitted_online_only_in_development(profile):
    config, mounts = configured(ServiceRole.WEB)
    config = replace(config, profile=profile)
    mounts.append(Mount(Path("/app/src"), True))
    if profile is DeploymentProfile.DEVELOPMENT:
        assert validate_mounts(config, mounts) is ServiceRole.WEB
    else:
        with pytest.raises(ConfigError, match="unrecognized mount"):
            validate_mounts(config, mounts)


@pytest.mark.parametrize("profile", PROFILES)
def test_source_mount_is_admitted_offline_only_in_development(tmp_path, profile):
    from parishkit.stewardship.offline_boundaries import validate_offline_mounts

    from .test_offline_boundaries import configuration as offline_configuration

    config, mounts = offline_configuration(tmp_path, ServiceRole.MIGRATION)
    config = replace(config, profile=profile)
    mounts.append(Mount(Path("/app/src"), True))
    if profile is DeploymentProfile.DEVELOPMENT:
        assert validate_offline_mounts(config, mounts) is ServiceRole.MIGRATION
    else:
        with pytest.raises(ConfigError, match="unrelated mount"):
            validate_offline_mounts(config, mounts)


@pytest.mark.parametrize("profile", PROFILES)
def test_source_mount_is_admitted_for_backup_only_in_development(tmp_path, profile):
    from parishkit.stewardship import backup_boundaries

    from .test_backup_boundaries import backup_configuration, mounts

    config = replace(backup_configuration(tmp_path), profile=profile)
    targets = backup_boundaries.backup_targets(config)
    extra = Mount(Path("/app/src"), True)
    if profile is DeploymentProfile.DEVELOPMENT:
        assert (
            backup_boundaries.validate_backup_mounts(config, mounts(targets, extra))
            is ServiceRole.BACKUP_WORKER
        )
    else:
        with pytest.raises(ConfigError, match="unrelated mount"):
            backup_boundaries.validate_backup_mounts(config, mounts(targets, extra))


# Row: development reload (Gunicorn's reloader and the static-files handler).
@pytest.mark.parametrize("profile", PROFILES)
def test_only_development_reloads_the_web_process(profile, monkeypatch, settings):
    from django.contrib.staticfiles.handlers import StaticFilesHandler

    configuration = configuration_for(profile)
    reload = profile is DeploymentProfile.DEVELOPMENT
    assert runtime_process.gunicorn_options(configuration)["reload"] is reload
    application = object()
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_web.configure_web", lambda configuration: None
    )
    monkeypatch.setattr("django.core.wsgi.get_wsgi_application", lambda: application)
    loaded = runtime_process.load_web_application(
        configuration, SimpleNamespace(check=lambda: None)
    )
    if reload:
        assert isinstance(loaded, StaticFilesHandler)
        assert loaded.application is application
    else:
        assert loaded is application


# Row: the existing "!= DEVELOPMENT" refusals in services.py and cli.py.
def test_scaffold_service_and_bind_opt_in_still_refuse_local(monkeypatch, capsys):
    """Both keep refusing everything that is not development web (the safe way)."""
    execute = Mock()
    monkeypatch.setattr(services.os, "execve", execute)
    assert services.run_service("local", "web") == 2
    execute.assert_not_called()
    assert "startup refused" in capsys.readouterr().err
    monkeypatch.setattr("parishkit.stewardship.cli.run_service", execute)
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "service",
                "--profile",
                "local",
                "--service-role",
                "web",
                "--bind-all-interfaces",
            ]
        )
    assert exc.value.code == 2
    execute.assert_not_called()


def test_local_is_a_cli_profile_and_round_trips_through_its_document(
    monkeypatch, capsys
):
    """``--profile local`` validates, and a rendered service document reloads."""
    for name in list(os.environ):
        if name == "PARISHKIT_ROOT" or name.startswith("PARISHKIT_STEWARDSHIP_"):
            monkeypatch.delenv(name)
    assert main(["validate-deployment", "--profile", "local"]) == 0
    assert '"deployment_syntax_valid": true' in capsys.readouterr().out
    # The development default origin is not an acceptable override for LOCAL.
    assert (
        main(
            [
                "validate-deployment",
                "--profile",
                "local",
                "--public-origin",
                "http://localhost:8000",
            ]
        )
        == 2
    )
    assert capsys.readouterr().err == "ERROR: deployment configuration is invalid\n"
    document = deployment_document(configuration_for(DeploymentProfile.LOCAL))
    reloaded = load_deployment(document=document, environ={})
    assert reloaded.profile is DeploymentProfile.LOCAL
    assert reloaded.public_origin == LOCAL_PUBLIC_ORIGIN
    assert reloaded.trusted_proxy_hops == 1


# Go-live origin verification: LOCAL admits exactly its one origin, with no
# resolver call, in both the parent check and the helper; every other profile
# still goes through the helper exactly as before.
WRONG_LOCAL_ORIGINS = [
    "https://localhost:8444",
    "https://localhost",
    "https://127.0.0.1:8443",
    "https://[::1]:8443",
    "http://localhost:8443",
    "https://parish.example:8443",
    "https://localhost:8443/path",
]


def origin_check_doubles(monkeypatch):
    """Stub the helper subprocess and the resolver so neither can be reached."""
    from parishkit.stewardship import origin_check, origin_check_worker

    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=b"ready\n"))
    monkeypatch.setattr(origin_check.subprocess, "run", run)
    lookup = Mock(return_value=[("stub",)])
    monkeypatch.setattr(origin_check_worker.socket, "getaddrinfo", lookup)
    return run, lookup


@pytest.mark.parametrize(
    "origin", [LOCAL_PUBLIC_ORIGIN, "https://localhost:8443/", "https://LOCALHOST:8443"]
)
def test_go_live_origin_check_admits_only_the_local_origin_without_dns(
    monkeypatch, origin
):
    """The one local origin verifies on its own; the helper is never started."""
    from parishkit.stewardship import origin_check, origin_check_worker

    run, lookup = origin_check_doubles(monkeypatch)
    assert origin_check.check_public_origin(origin, DeploymentProfile.LOCAL) is True
    # The helper applies the same rule on its own if it is ever handed LOCAL.
    assert origin_check_worker.resolve({"origin": origin, "profile": "local"}) is True
    run.assert_not_called()
    lookup.assert_not_called()


@pytest.mark.parametrize("origin", WRONG_LOCAL_ORIGINS)
def test_go_live_origin_check_refuses_every_other_local_origin(monkeypatch, origin):
    """Wrong port, host, scheme or path is refused before any helper or DNS."""
    from parishkit.stewardship import origin_check, origin_check_worker

    run, lookup = origin_check_doubles(monkeypatch)
    with pytest.raises(ConfigError):
        origin_check.check_public_origin(origin, DeploymentProfile.LOCAL)
    with pytest.raises(ConfigError):
        origin_check_worker.resolve({"origin": origin, "profile": "local"})
    run.assert_not_called()
    lookup.assert_not_called()


def test_go_live_origin_check_is_unchanged_outside_local(monkeypatch):
    """Production, development and test still reach the helper, verdict and all.

    A production deployment naming localhost:8443 is not given the LOCAL rule:
    it is sent to the helper like any other production origin, and the helper
    resolves it rather than admitting it.
    """
    from parishkit.stewardship import origin_check, origin_check_worker

    run, lookup = origin_check_doubles(monkeypatch)
    expected = [
        ("https://parish.example", DeploymentProfile.PRODUCTION),
        (LOCAL_PUBLIC_ORIGIN, DeploymentProfile.PRODUCTION),
        ("http://localhost:8000", DeploymentProfile.DEVELOPMENT),
        ("http://localhost:8000", DeploymentProfile.TEST),
    ]
    for origin, profile in expected:
        assert origin_check.check_public_origin(origin, profile) is True
    assert [json.loads(call.kwargs["input"]) for call in run.call_args_list] == [
        {"origin": origin, "profile": profile.value} for origin, profile in expected
    ]
    # The helper's verdict is still the resolver's, including for localhost.
    for origin, profile in expected:
        assert origin_check_worker.resolve({"origin": origin, "profile": profile.value})
    assert [call.args[:2] for call in lookup.call_args_list] == [
        ("parish.example", 443),
        ("localhost", 8443),
        ("localhost", 8000),
        ("localhost", 8000),
    ]
    lookup.return_value = []
    assert not origin_check_worker.resolve(
        {"origin": LOCAL_PUBLIC_ORIGIN, "profile": "production"}
    )
    # Production still refuses a plain-HTTP origin before any resolution.
    with pytest.raises(ConfigError):
        origin_check.check_public_origin(
            "http://parish.example", DeploymentProfile.PRODUCTION
        )


def test_go_live_origin_helper_answers_ready_for_local_without_a_resolver(monkeypatch):
    """End to end through the helper's protocol: only the fixed verdict is written."""
    from parishkit.stewardship import origin_check_worker

    _, lookup = origin_check_doubles(monkeypatch)
    lookup.side_effect = OSError("the resolver must not be consulted")
    verdicts = []
    for origin in (LOCAL_PUBLIC_ORIGIN, "https://localhost:8444"):
        payload = json.dumps({"origin": origin, "profile": "local"}).encode()
        output = io.BytesIO()
        monkeypatch.setattr(
            origin_check_worker.sys,
            "stdin",
            SimpleNamespace(buffer=io.BytesIO(payload)),
        )
        monkeypatch.setattr(
            origin_check_worker.sys, "stdout", SimpleNamespace(buffer=output)
        )
        origin_check_worker.main()
        verdicts.append(output.getvalue())
    assert verdicts == [b"ready\n", b"unavailable\n"]
    lookup.assert_not_called()


# Production invariant: no LOCAL branch in the schema's SQL.
def test_schema_sql_has_no_deployment_profile_branch():
    """Guards and functions know campaign modes, never the deployment profile.

    A text check: no schema file names a deployment profile, or the local or
    development profile as a SQL literal. Campaign mode literals ('testing',
    'production') are a different concept and are not what this looks for;
    'test' is not matched either, as it would collide with the submission
    mode literal.
    """
    schema = Path(deployment_module.__file__).with_name("schema")
    pattern = re.compile(
        r"'local'|'development'|deployment[_ ]profile|behind_proxy", re.IGNORECASE
    )
    files = sorted(schema.glob("*.sql"))
    assert {"guards.sql", "functions.sql"} <= {path.name for path in files}
    for path in files:
        assert pattern.search(path.read_text()) is None, path.name
