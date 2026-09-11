"""The resolved runtime never turns broad host trees into container authority."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import (
    SECRET_NAMES,
    DeploymentProfile,
    load_deployment,
)
from parishkit.stewardship.runtime_ingress import render_caddy
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.runtime_topology import CADDY_IMAGE, render_runtime

IMAGE = "ghcr.io/example/parishkit/parishkit@sha256:" + "a" * 64


def configuration_at(path, *, production=False):
    """Use no real secrets or host provisioning for pure rendering tests."""
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(path)})
    return replace(
        configuration,
        profile=(
            DeploymentProfile.PRODUCTION
            if production
            else DeploymentProfile.DEVELOPMENT
        ),
        public_origin="https://parish.example"
        if production
        else "http://localhost:8010",
        trusted_proxy_hops=int(production),
        valkey=replace(configuration.valkey, password_file=path / "broker-password"),
    )


@pytest.mark.parametrize("production", [False, True])
def test_rendered_foundation_enforces_individual_mounts_and_profiles(
    tmp_path, production
):
    """Every active process is unprivileged; offline authority is explicit-only."""
    configuration = configuration_at(tmp_path, production=production)
    compose, documents = render_runtime(
        configuration,
        image=IMAGE if production else "parishkit-stewardship:development",
    )
    services, layout = compose["services"], RuntimeLayout(configuration)
    for name, service in services.items():
        assert service["user"] == "10001:10001"
        assert service["read_only"] is True
        assert service["init"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        for mount in service["volumes"]:
            assert mount["bind"]["create_host_path"] is False
            assert Path(mount["source"]) not in {
                configuration.paths["config"],
                configuration.paths["credentials"],
                configuration.paths.root,
                Path("/var/run/docker.sock"),
            }
        if name in {"bootstrap", "migration", "admin-recovery"}:
            assert service["profiles"] == [name]
        if name not in {"caddy", "web"}:
            assert "ports" not in service
    web = services["web"]
    mounts = {Path(item["source"]): item for item in web["volumes"]}
    assert mounts[configuration.paths["authority"]]["read_only"] is True
    assert mounts[layout.database_password("download")]["read_only"] is True
    assert layout.credential("token_private") not in mounts
    assert layout.credential("google_workspace") not in mounts
    assert layout.interlock in mounts
    for target in SECRET_NAMES - {"handoff_private"}:
        name = "credential-installer-" + target.replace("_", "-")
        mounts = services[name]["volumes"]
        writable = [item["source"] for item in mounts if not item["read_only"]]
        assert writable == [str(layout.credential_directory(target))]
        assert str(layout.handoff(target)) in [item["source"] for item in mounts]
    for path, document in documents.items():
        if path.suffix != ".yaml":
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document))
        assert load_deployment(path, environ={}).configuration_file == path
    if production:
        caddy = services["caddy"]
        assert caddy["image"] == CADDY_IMAGE
        assert caddy["ports"] == ["80:8080", "443:8443"]
        assert set(caddy["networks"]) == {"proxy", "ingress"}
        assert set(services["postgres"]["networks"]) == {"backend"}
        assert set(services["valkey"]["networks"]) == {"backend"}
        assert "ports" not in web
        assert documents[layout.service_directory / "Caddyfile"] == render_caddy(
            configuration
        )
    else:
        assert "caddy" not in services
        assert web["ports"] == ["127.0.0.1:8010:8000"]


def test_development_source_is_read_only_and_live(tmp_path):
    """Only the development source directory can be bind-mounted into the image."""
    configuration = configuration_at(tmp_path)
    compose, _ = render_runtime(
        configuration, image="parishkit-stewardship:development", checkout=tmp_path
    )
    for name, service in compose["services"].items():
        if name in {"postgres", "valkey"}:
            continue
        assert any(
            mount["source"] == str(tmp_path / "src")
            and mount["target"] == "/app/src"
            and mount["read_only"]
            for mount in service["volumes"]
        )
    configuration = replace(configuration, public_origin="http://[::1]:8011")
    compose, _ = render_runtime(
        configuration, image="parishkit-stewardship:development"
    )
    assert compose["services"]["web"]["ports"] == ["[::1]:8011:8000"]


@pytest.mark.parametrize(
    "origin",
    ["https://127.0.0.1", "https://localhost", "https://example.test:8443"],
)
def test_production_ingress_requires_standard_public_dns_origin(tmp_path, origin):
    """Do not advertise successful ACME setup for unsupported address/port inputs."""
    configuration = replace(
        configuration_at(tmp_path, production=True), public_origin=origin
    )
    with pytest.raises(ConfigError):
        render_runtime(configuration, image=IMAGE)


def test_runtime_refuses_unbounded_or_ambiguous_configuration(tmp_path):
    """A valid deployment parser value may still be unsuitable for concrete Compose."""
    configuration = configuration_at(tmp_path, production=True)
    for kwargs in (
        {"image": "ghcr.io/example/parishkit/parishkit:latest"},
        {"image": IMAGE, "checkout": tmp_path},
    ):
        with pytest.raises(ConfigError):
            render_runtime(configuration, **kwargs)
    for replacement in (
        replace(configuration.postgres, host="external.example"),
        replace(configuration.postgres, port=55432),
    ):
        with pytest.raises(ConfigError):
            render_runtime(replace(configuration, postgres=replacement), image=IMAGE)


def test_ingress_has_ordered_denials_bounded_transport_and_no_private_logs(tmp_path):
    """Log filtering applies to errors as well as successful HTTP requests."""
    configuration = configuration_at(tmp_path, production=True)
    output = render_caddy(configuration)
    assert output.index("respond @internal 404") < output.index("reverse_proxy")
    assert "path /health/* /metrics /metrics/*" in output
    assert output.count("request delete") == 2
    assert output.count("resp_headers delete") == 2
    assert output.count("error delete") == 2
    assert "max_size 6MB" in output
    assert "admin off" in output
    assert "read_timeout 380s" in output
    assert "health_uri" not in output
    assert "https://acme-v02.api.letsencrypt.org/directory" in output
