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
def test_operational_fixture_does_not_mount_host_temporary_paths(tmp_path, production):
    """Linux host staging cannot make writable /tmp an ancestor of credentials."""
    from .test_operational_compose import seed_runtime

    staging = tmp_path / "host-temporary-seed"
    configuration, compose = seed_runtime(staging, production=production)
    container_root = configuration.paths.root
    assert container_root == Path("/opt/parishkit-integration")
    for service in compose["services"].values():
        for mount in service["volumes"]:
            source, target = Path(mount["source"]), Path(mount["target"])
            assert not target.is_relative_to(staging)
            if source.is_relative_to(container_root):
                seed = staging / source.relative_to(container_root)
                # Bootstrap generates purpose keys later; their private parent
                # already exists, but the seed must not fabricate their values.
                assert seed.exists() or seed.parent.exists()
    layout = RuntimeLayout(configuration)
    for name in ("web", "config-installer", "bootstrap"):
        document = staging / (layout.service_directory / f"{name}.yaml").relative_to(
            container_root
        )
        loaded = load_deployment(document, environ={})
        assert loaded.paths.root == container_root
        assert str(staging) not in document.read_text()


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
        assert service.get("cap_add", []) == (
            ["NET_BIND_SERVICE"] if name == "caddy" else []
        )
        assert service["security_opt"] == ["no-new-privileges:true"]
        for mount in service["volumes"]:
            assert mount["bind"]["create_host_path"] is False
            assert Path(mount["source"]) not in {
                configuration.paths["config"],
                configuration.paths["credentials"],
                configuration.paths.root,
                Path("/var/run/docker.sock"),
            }
        if name in {"bootstrap", "migration", "admin-recovery", "database-provision"}:
            assert service["profiles"] == [name]
            assert service["restart"] == "no"
        else:
            assert service["restart"] == ("unless-stopped" if production else "no")
            assert service["healthcheck"]["timeout"]
        if name not in {"caddy", "web"}:
            assert "ports" not in service
    web = services["web"]
    mounts = {Path(item["source"]): item for item in web["volumes"]}
    assert mounts[configuration.paths["authority"]]["read_only"] is True
    assert mounts[layout.database_password("download")]["read_only"] is True
    assert layout.credential("token_private") not in mounts
    assert layout.credential("google_workspace") not in mounts
    assert layout.interlock in mounts
    for name in ("worker", "scheduler", "mail-dispatch"):
        background = services[name]
        selected = documents[layout.service_directory / f"{name}.yaml"]
        mounts = {Path(item["source"]): item for item in background["volumes"]}
        assert background["command"] == [
            "runtime",
            "--config",
            str(layout.service_directory / f"{name}.yaml"),
        ]
        assert mounts[layout.valkey_password(name)]["read_only"]
        assert mounts[layout.database_password(name)]["read_only"]
        assert mounts[configuration.paths["authority"]]["read_only"]
        assert layout.database_password("web") not in mounts
        assert layout.valkey_password("web") not in mounts
        assert (layout.credential("token_private") in mounts) is (
            name == "mail-dispatch"
        )
        assert (layout.credential("google_workspace") in mounts) is (
            name == "mail-dispatch"
        )
        assert layout.credential("token_public") in mounts
        if name == "worker":
            assert mounts[configuration.paths["media"]]["read_only"] is False
        else:
            assert configuration.paths["media"] not in mounts
        assert selected["deployment"]["service_role"] == name
        assert set(background["networks"]) == (
            {"backend", "application-egress"} if name != "scheduler" else {"backend"}
        )
        assert (layout.credential("parishsoft") in mounts) is (name == "worker")
    for target in SECRET_NAMES - {"handoff_private"}:
        name = "credential-installer-" + target.replace("_", "-")
        mounts = services[name]["volumes"]
        writable = [item["source"] for item in mounts if not item["read_only"]]
        assert writable == [str(layout.credential_directory(target))]
        assert str(layout.handoff(target)) in [item["source"] for item in mounts]
        assert ("application-egress" in services[name]["networks"]) is (
            target in {"parishsoft", "google_workspace", "slack"}
        )
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


@pytest.mark.parametrize("mode", ["initial", "configured", "configured-slack"])
def test_provider_modes_keep_service_identity_and_individual_mounts(tmp_path, mode):
    """Initial startup needs no provider file; recreation selects only owned files."""
    configuration = configuration_at(tmp_path)
    compose, documents = render_runtime(
        configuration, image="parishkit-stewardship:development", provider_mode=mode
    )
    layout = RuntimeLayout(configuration)
    assert {"worker", "mail-dispatch"} <= compose["services"].keys()
    for name, providers in (
        ("worker", {"parishsoft", "slack"}),
        ("mail-dispatch", {"google_workspace"}),
    ):
        service = compose["services"][name]
        mounted = {Path(item["target"]): item for item in service["volumes"]}
        document = documents[Path(service["command"][-1])]["deployment"]
        assert document["service_role"] == name
        assert document["postgres"]["user"] == "pk_stewardship_" + name.replace(
            "-", "_"
        )
        expected = set() if mode == "initial" else providers - {"slack"}
        if mode == "configured-slack" and name == "worker":
            expected.add("slack")
        assert (
            set(document["secrets"]) & {"parishsoft", "slack", "google_workspace"}
            == expected
        )
        for provider in providers:
            path = layout.credential(provider)
            assert (path in mounted) is (provider in expected)
            assert path.parent not in mounted
            if provider in expected:
                assert mounted[path]["read_only"] is True


@pytest.mark.parametrize("mode", [True, None, "all", "../../worker"])
def test_provider_mode_is_closed_before_rendering(tmp_path, mode):
    """A caller cannot invent a service/configuration-path mode."""
    with pytest.raises(ConfigError, match="mount mode"):
        render_runtime(
            configuration_at(tmp_path),
            image="parishkit-stewardship:development",
            provider_mode=mode,
        )


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


def test_individual_sql_overrides_reach_provisioner_and_only_their_consumers(tmp_path):
    """A generated profile must not silently return to default password paths."""
    configuration = configuration_at(tmp_path)
    selected = tmp_path / "selected-sql"
    overrides = {
        "operator": selected / "operator",
        "config-installer": selected / "configuration",
        "credential-installer-metrics": selected / "metrics",
    }
    configuration = replace(
        configuration,
        postgres=replace(
            configuration.postgres,
            password_file=selected / "web",
            download_password_file=selected / "download",
            password_files=overrides,
        ),
    )
    compose, documents = render_runtime(
        configuration, image="parishkit-stewardship:development"
    )
    expected = overrides | {"web": selected / "web", "download": selected / "download"}
    services = compose["services"]
    provisioning_mounts = {
        Path(mount["source"]) for mount in services["database-provision"]["volumes"]
    }
    assert set(expected.values()) <= provisioning_mounts
    for name in ("web", "config-installer", "credential-installer-metrics"):
        mounts = {Path(mount["source"]) for mount in services[name]["volumes"]}
        allowed = {expected[name]}
        if name == "web":
            allowed.add(expected["download"])
        assert mounts & set(expected.values()) == allowed
    for path, document in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document))
        loaded = load_deployment(path, environ={})
        assert loaded.configuration_file == path
        assert loaded.postgres.password_files == expected


def test_disagreeing_scalar_and_identity_password_overrides_are_refused(tmp_path):
    """Neither a scalar nor map silently wins when they nominate different files."""
    configuration = configuration_at(tmp_path)
    configuration = replace(
        configuration,
        postgres=replace(
            configuration.postgres,
            password_file=tmp_path / "one",
            password_files={"web": tmp_path / "two"},
        ),
    )
    with pytest.raises(ConfigError, match="disagree"):
        render_runtime(configuration, image="parishkit-stewardship:development")


def test_renderer_refuses_unsupported_multi_container_runtime(tmp_path):
    """Rendering and provisioning share the same operational support boundary."""
    configuration = configuration_at(tmp_path)
    configuration = replace(
        configuration,
        runtime_budget=replace(
            configuration.runtime_budget,
            replicas=2,
            auxiliary_connections=24,
            database_connections=200,
        ),
    )
    with pytest.raises(ConfigError, match="one web container"):
        render_runtime(configuration, image="parishkit-stewardship:development")
