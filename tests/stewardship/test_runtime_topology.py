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
from parishkit.stewardship.runtime_ingress import MAINTENANCE_PAGE, render_caddy
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.runtime_topology import CADDY_IMAGE, render_runtime

IMAGE = "ghcr.io/example/parishkit/stewardship@sha256:" + "a" * 64


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
            assert Path(mount["source"]) != configuration.paths.root
            assert Path(mount["source"]) != Path("/var/run/docker.sock")
            # Only the one-shot backup profile reads the whole configuration and
            # credentials trees, read-only, to seal them to the operator's key.
            if name != "backup-worker":
                assert Path(mount["source"]) not in {
                    configuration.paths["config"],
                    configuration.paths["credentials"],
                }
        if name == "backup-worker":
            mounts = {Path(m["source"]): m["read_only"] for m in service["volumes"]}
            assert mounts[configuration.paths["config"]] is True
            assert mounts[configuration.paths["credentials"]] is True
            assert mounts[configuration.paths["backups"]] is False
            assert service["command"][0] == "backup"
            # Egress carries the off-site copies to Google Drive.
            assert set(service["networks"]) == {"backend", "application-egress"}
        if name in {
            "bootstrap",
            "migration",
            "admin-recovery",
            "database-provision",
            "backup-worker",
        }:
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
        # Rotatable integration credentials mount their read-only directory so a
        # running consumer sees an installed replacement without recreation.
        assert (layout.credential_directory("google_workspace") in mounts) is (
            name == "mail-dispatch"
        )
        assert layout.credential("google_workspace") not in mounts
        assert layout.credential("token_public") in mounts
        for path in ("media", "reports"):
            if name == "worker":
                assert mounts[configuration.paths[path]]["read_only"] is False
            else:
                assert configuration.paths[path] not in mounts
        assert selected["deployment"]["service_role"] == name
        assert set(background["networks"]) == (
            {"backend", "application-egress"} if name != "scheduler" else {"backend"}
        )
        assert (layout.credential_directory("parishsoft") in mounts) is (
            name == "worker"
        )
        for directory in map(layout.credential_directory, ("parishsoft", "slack")):
            assert mounts.get(directory, {"read_only": True})["read_only"] is True
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
def test_provider_modes_keep_service_identity_and_owned_mounts(tmp_path, mode):
    """Initial startup needs no provider file; configured mounts every owned one."""
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
        # A configured worker always mounts Slack's folder, so Slack can be
        # added after setup without switching compose files.
        expected = set() if mode == "initial" else providers
        assert (
            set(document["secrets"]) & {"parishsoft", "slack", "google_workspace"}
            == expected
        )
        for provider in providers:
            path = layout.credential(provider)
            assert (path.parent in mounted) is (provider in expected)
            assert path not in mounted
            if provider in expected:
                assert mounted[path.parent]["read_only"] is True


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
        {"image": "ghcr.io/example/parishkit/stewardship:latest"},
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


def test_ingress_serves_a_self_contained_maintenance_page_only_when_web_is_down(
    tmp_path,
):
    """Caddy answers an unreachable web with its own page, never a real error.

    Only the proxy's own upstream failures (502 dial or reset, 503 no
    upstream) reach handle_errors; application responses, 500s included, pass
    through, and a slow request (504) is not dressed up as an upgrade (#162).
    """
    configuration = configuration_at(tmp_path, production=True)
    output = render_caddy(configuration)
    block = output[output.index("handle_errors") :]
    assert output.index("reverse_proxy") < output.index("handle_errors")
    assert block.startswith("handle_errors 502 503 {")
    assert "504" not in block.splitlines()[0]
    assert "MAINTENANCE 503" in block
    assert 'Retry-After "120"' in block
    assert 'Cache-Control "no-store"' in block
    assert "default-src 'none'" in block
    # Self-contained: no scripts, no application assets, and no braces the
    # Caddyfile placeholder syntax would claim.
    assert "<script" not in MAINTENANCE_PAGE
    assert "/static/" not in MAINTENANCE_PAGE
    assert "{" not in MAINTENANCE_PAGE and "}" not in MAINTENANCE_PAGE
    assert "We&rsquo;re updating the site" in MAINTENANCE_PAGE


def test_ingress_keeps_running_through_offline_work(tmp_path):
    """Caddy holds no startup-interlock lease, so upgrades need not stop it."""
    configuration = configuration_at(tmp_path, production=True)
    compose, _ = render_runtime(configuration, image=IMAGE)
    caddy = compose["services"]["caddy"]
    layout = RuntimeLayout(configuration)
    assert str(layout.interlock) not in [m["source"] for m in caddy["volumes"]]
    assert "startup.lock" not in " ".join(caddy["command"])
    assert "flock" not in " ".join(caddy["command"])
    # web still holds the lease, so nothing behind caddy runs offline.
    web = compose["services"]["web"]
    assert str(layout.interlock) in [m["source"] for m in web["volumes"]]


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


@pytest.mark.parametrize("mode", ["initial", "configured", "configured-slack"])
def test_every_rendered_service_document_carries_the_alert_policy(tmp_path, mode):
    """Services load the renderer's documents, so every one must keep the policy.

    This checks the documents render_runtime actually writes, in every provider
    mode, for every role (online services, the backup worker, migration,
    bootstrap and credential installers), not a hand-built configuration.
    """
    from parishkit.stewardship.jobs.operational_policy import IncidentPolicy

    policy = IncidentPolicy(
        suppression_seconds=120, escalation_seconds=180, source_stale_seconds=2400
    )
    configuration = replace(configuration_at(tmp_path), operational_alerts=policy)
    _, documents = render_runtime(
        configuration, image="parishkit-stewardship:development", provider_mode=mode
    )
    rendered = [
        value
        for value in documents.values()
        if isinstance(value, dict) and "deployment" in value
    ]
    roles = {document["deployment"]["service_role"] for document in rendered}
    assert {"web", "worker", "scheduler", "mail-dispatch", "backup-worker"} <= roles
    for document in rendered:
        assert document["deployment"]["operational_alerts"] == {
            "suppression_seconds": 120,
            "escalation_seconds": 180,
            "source_stale_seconds": 2400,
        }


@pytest.mark.parametrize("provider_mode", ["initial", "configured", "configured-slack"])
def test_worker_receives_the_source_loss_override_from_the_operator_shell(
    tmp_path, provider_mode
):
    """#320: the runbook's one-refresh override reaches the source worker.

    It is empty unless the operator's shell exports it when running Compose,
    which keeps the 25% default.
    """
    compose, _ = render_runtime(
        configuration_at(tmp_path, production=True),
        image=IMAGE,
        provider_mode=provider_mode,
    )
    environment = compose["services"]["worker"]["environment"]
    assert environment["PARISHKIT_SOURCE_MAX_DROP_PERCENT"] == (
        "${PARISHKIT_SOURCE_MAX_DROP_PERCENT:-}"
    )


@pytest.mark.parametrize("production", [False, True])
def test_every_service_caps_its_container_log(tmp_path, production):
    """#326 M1: no container, database and ingress included, logs without limit.

    Docker's default json-file driver never rotates, so each service keeps
    at most five 10 MB files.
    """
    compose, _ = render_runtime(
        configuration_at(tmp_path, production=production),
        image=IMAGE if production else "parishkit-stewardship:development",
    )
    services = compose["services"]
    assert {"postgres", "valkey", "web", "worker", "backup-worker"} <= set(services)
    assert production == ("caddy" in services)
    for name, service in services.items():
        assert service["logging"] == {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "5"},
        }, name
    # Each service owns its copy, so a later edit to one cannot move another.
    assert services["web"]["logging"] is not services["worker"]["logging"]


def test_static_files_are_revalidated_on_every_use(tmp_path):
    """#326 M4: fixed-name scripts and styles never outlive a hotfix in a cache."""
    output = render_caddy(configuration_at(tmp_path, production=True))
    block = output[output.index("handle_path /static/* {") :]
    block = block[: block.index("}")]
    assert 'header Cache-Control "no-cache"' in block
    assert "file_server" in block
    # Only static files: the application sets its own headers.
    assert output.count("Cache-Control") == 2  # this and the maintenance page
