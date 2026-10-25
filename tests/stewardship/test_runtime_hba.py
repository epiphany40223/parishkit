"""PostgreSQL's connection-source rules follow the rendered topology (#2111).

Operations spec, "Database connection sources": each login is accepted only
from its own service's fixed address, only for the deployment's database,
with scram-sha-256; the operator reaches TCP only from database-provision and
the socket only by docker exec; a final reject closes the file.
"""

from dataclasses import replace
from ipaddress import ip_address, ip_network

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import DeploymentProfile, load_deployment
from parishkit.stewardship.deployment_documents import deployment_document
from parishkit.stewardship.inspect_login import INSPECT_LOGIN
from parishkit.stewardship.runtime_hba import (
    HBA_FILE,
    HBA_MOUNT,
    OPERATOR,
    render_hba,
    source_rules,
)
from parishkit.stewardship.runtime_identities import database_identities
from parishkit.stewardship.runtime_network import (
    DEFAULT_OFFLINE,
    OFFLINE_PROFILES,
    RuntimeNetwork,
    network_document,
    parse_network,
)
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.runtime_topology import DEVELOPMENT_IMAGE, render_runtime

from .test_local_profile import (
    FIXTURES,
    GOLDEN_ROOT,
    LOCAL_IMAGE,
    PROFILES,
    configuration_for,
)
from .test_runtime_topology import IMAGE as PRODUCTION_IMAGE

IMAGES = {
    DeploymentProfile.PRODUCTION: PRODUCTION_IMAGE,
    DeploymentProfile.LOCAL: LOCAL_IMAGE,
}
MODES = ("initial", "configured", "configured-slack")
INVENTORY = {OPERATOR, *(entry[1] for entry in database_identities())}
# Which rendered service holds each login: the table in the spec.
HOLDERS = {
    "pk_stewardship_web": "web",
    "pk_stewardship_download": "web",
    "pk_stewardship_worker": "worker",
    "pk_stewardship_mail_dispatch": "mail-dispatch",
    "pk_stewardship_scheduler": "scheduler",
    "pk_stewardship_config_installer": "config-installer",
    "pk_stewardship_bootstrap": "bootstrap",
    "pk_stewardship_migration": "migration",
    "pk_stewardship_admin_recovery": "admin-recovery",
    "pk_stewardship_backup_worker": "backup-worker",
    OPERATOR: "database-provision",
}


def rendering(profile, mode="configured", root=GOLDEN_ROOT, **changes):
    """The profile's configuration, Compose document and pg_hba.conf text."""
    configuration = replace(configuration_for(profile, root), **changes)
    compose, documents = render_runtime(
        configuration,
        image=IMAGES.get(profile, DEVELOPMENT_IMAGE),
        provider_mode=mode,
    )
    hba = documents[RuntimeLayout(configuration).hba_directory / "pg_hba.conf"]
    return configuration, compose, hba


def host_lines(hba):
    """The TCP rules, split into their fields."""
    return [line.split() for line in hba.splitlines() if line.startswith("host ")]


def holder(login):
    """The service name that holds a login (credential installers by target)."""
    if login.startswith("pk_stewardship_credential_"):
        target = login.removeprefix("pk_stewardship_credential_")
        return "credential-installer-" + target.replace("_", "-")
    return HOLDERS[login]


def expected_source(configuration, compose, name):
    """The one source a service's containers connect from, read from Compose."""
    if name in OFFLINE_PROFILES:
        return configuration.runtime_network.offline_source(name)
    networks = compose["services"][name]["networks"]
    return networks["backend"]["ipv4_address"] + "/32"


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("mode", MODES)
def test_every_login_has_exactly_its_own_service_source(profile, mode):
    """One rule per login and source, scram-sha-256, the deployment's database."""
    configuration, compose, hba = rendering(profile, mode)
    rules = host_lines(hba)
    assert rules[-1] == ["host", "all", "all", "all", "reject"]
    assert ["host", "all", "all", "all", "reject"] not in rules[:-1]
    expected = {
        (login, expected_source(configuration, compose, holder(login)))
        for login in INVENTORY
    }
    if profile is DeploymentProfile.LOCAL:
        seeder = compose["x-local-seeder"]["ipv4_address"] + "/32"
        expected |= {
            ("pk_stewardship_web", seeder),
            ("pk_stewardship_download", seeder),
        }
    else:
        assert "x-local-seeder" not in compose
    found = [(fields[2], fields[3]) for fields in rules[:-1]]
    assert len(found) == len(set(found))
    assert set(found) == expected
    for fields in rules[:-1]:
        assert fields[1] == '"stewardship"'
        assert fields[4] == "scram-sha-256"
        assert len(fields) == 5


@pytest.mark.parametrize("profile", PROFILES)
def test_no_tcp_trust_and_the_socket_admits_only_the_operator_and_inspector(profile):
    """The socket trusts the operator and inspector only; TCP never trusts."""
    _, _, hba = rendering(profile)
    rules = [line for line in hba.splitlines() if line and not line.startswith("#")]
    assert rules[:3] == [
        f"local all {OPERATOR} trust",
        f'local "stewardship" {INSPECT_LOGIN} trust',
        "local all all reject",
    ]
    assert not any(line.startswith("local") for line in rules[3:])
    for line in rules[3:]:
        assert line.startswith("host ")
        assert "trust" not in line.split() and "md5" not in line.split()
        assert "hostssl" not in line and "hostnossl" not in line
    operator = [line for line in rules if f" {OPERATOR} " in line + " "]
    assert operator[1:] == [
        f'host "stewardship" {OPERATOR} '
        f"{RuntimeNetwork().offline_source('database-provision')} scram-sha-256"
    ]
    # No rule for token-key-rotation, which is not rendered and has no login.
    assert "rotation" not in hba


def test_production_rules_match_the_golden_file_and_carry_no_seeder():
    """The Production file is pinned in full; LOCAL adds only its seeder lines."""
    _, _, production = rendering(DeploymentProfile.PRODUCTION)
    assert production == (FIXTURES / "production-pg_hba.golden").read_text()
    _, compose, local = rendering(DeploymentProfile.LOCAL)
    seeder = compose["x-local-seeder"]["ipv4_address"]
    assert seeder not in production
    extra = [line for line in local.splitlines() if line not in production.splitlines()]
    assert extra == [
        f'host "stewardship" pk_stewardship_web {seeder}/32 scram-sha-256',
        f'host "stewardship" pk_stewardship_download {seeder}/32 scram-sha-256',
    ]


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("mode", MODES)
def test_every_container_is_unprivileged_and_on_fixed_or_one_shot_addresses(
    profile, mode
):
    """No container can claim another's address: no NET_ADMIN/NET_RAW, no host net.

    Every container drops all capabilities and adds back none but Caddy's
    NET_BIND_SERVICE; every ``backend`` member has a unique fixed address in
    the lower half, and every one-shot profile is on its own network only.
    """
    configuration, compose, _ = rendering(profile, mode)
    network = configuration.runtime_network
    backend = ip_network(network.backend)
    lower = list(backend.subnets(prefixlen_diff=1))[0]
    addresses = []
    for name, service in compose["services"].items():
        assert service["cap_drop"] == ["ALL"], name
        assert set(service.get("cap_add", [])) <= {"NET_BIND_SERVICE"}, name
        assert "NET_ADMIN" not in service.get("cap_add", [])
        assert "NET_RAW" not in service.get("cap_add", [])
        assert "network_mode" not in service and "privileged" not in service, name
        assert "no-new-privileges:true" in service["security_opt"]
        if "backend" in service["networks"]:
            address = ip_address(service["networks"]["backend"]["ipv4_address"])
            assert address in lower and int(address) - int(backend[0]) >= 2, name
            addresses.append(address)
        if name in OFFLINE_PROFILES:
            assert set(service["networks"]) - {"application-egress"} == {
                f"offline-{name}"
            }
            assert ("application-egress" in service["networks"]) is (
                name == "backup-worker"
            )
    if "x-local-seeder" in compose:
        addresses.append(ip_address(compose["x-local-seeder"]["ipv4_address"]))
    assert len(addresses) == len(set(addresses))
    ipam = compose["networks"]["backend"]["ipam"]["config"]
    assert ipam == [
        {
            "subnet": network.backend,
            "gateway": str(backend[1]),
            "ip_range": str(list(backend.subnets(prefixlen_diff=1))[1]),
        }
    ]


@pytest.mark.parametrize("profile", PROFILES)
def test_one_shot_networks_exclude_the_gateway_and_hold_postgres(profile):
    """Each /28: gateway at 1, PostgreSQL at 2, containers from the upper /29."""
    configuration, compose, _ = rendering(profile)
    postgres = compose["services"]["postgres"]
    subnets = []
    for profile_name in OFFLINE_PROFILES:
        definition = compose["networks"][f"offline-{profile_name}"]
        assert definition["internal"] is True
        (ipam,) = definition["ipam"]["config"]
        subnet = ip_network(ipam["subnet"])
        allowed = ip_network(ipam["ip_range"])
        gateway = ip_address(ipam["gateway"])
        assert subnet.prefixlen == 28 and subnet.subnet_of(
            ip_network(configuration.runtime_network.offline)
        )
        assert gateway == subnet[1] and gateway not in allowed
        assert allowed == list(subnet.subnets(prefixlen_diff=1))[1]
        address = postgres["networks"][f"offline-{profile_name}"]["ipv4_address"]
        assert ip_address(address) == subnet[2] and ip_address(address) not in allowed
        members = [
            name
            for name, service in compose["services"].items()
            if f"offline-{profile_name}" in service["networks"]
        ]
        assert sorted(members) == sorted([profile_name, "postgres"])
        subnets.append(subnet)
    assert all(
        not a.overlaps(b) for i, a in enumerate(subnets) for b in subnets[i + 1 :]
    )


def test_postgres_reads_the_mounted_rules_and_never_its_data_directory_file():
    """A read-only directory mount and -c hba_file; the stock file is untouched."""
    configuration, compose, _ = rendering(DeploymentProfile.PRODUCTION)
    postgres = compose["services"]["postgres"]
    command = postgres["command"]
    assert command[command.index(f"hba_file={HBA_FILE}") - 1] == "-c"
    assert HBA_FILE == HBA_MOUNT + "/pg_hba.conf"
    (mount,) = [item for item in postgres["volumes"] if item["target"] == HBA_MOUNT]
    assert mount["read_only"] is True
    assert mount["source"] == str(RuntimeLayout(configuration).hba_directory)
    assert not any("pg_hba" in item["target"] for item in postgres["volumes"])


def test_wider_and_narrower_backends_keep_every_address_in_the_lower_half():
    """A /26 backend still fits every LOCAL member below its dynamic range."""
    network = RuntimeNetwork(backend="10.42.0.0/26", proxy="10.42.1.0/24")
    configuration, compose, hba = rendering(
        DeploymentProfile.LOCAL, runtime_network=network
    )
    assert compose["networks"]["backend"]["ipam"]["config"][0]["ip_range"] == (
        "10.42.0.32/27"
    )
    assert " pk_stewardship_web 10.42.0.4/32 " in hba
    assert compose["x-local-seeder"] == {"ipv4_address": "10.42.0.30"}
    with pytest.raises(ConfigError, match="fixed range"):
        network.backend_address(32)
    with pytest.raises(ConfigError, match="fixed range"):
        network.backend_address(1)


def test_the_database_name_is_quoted_and_an_unsafe_one_is_refused():
    """A keyword is matched literally; a quote or control character is refused."""
    base = configuration_for(DeploymentProfile.PRODUCTION)
    for name in ("all", "replication", "+group", "Mixed Case"):
        _, _, hba = rendering(
            DeploymentProfile.PRODUCTION, postgres=replace(base.postgres, name=name)
        )
        rules = [line for line in hba.splitlines() if line.startswith("host ")]
        assert all(line.startswith(f'host "{name}" ') for line in rules[:-1])
    # A quoted leading "/" is still a regular expression: "/.*" matches all.
    for name in ('a"b', "a\nb", "", "/.*", "/stewardship"):
        with pytest.raises(ConfigError, match="database name"):
            rendering(
                DeploymentProfile.PRODUCTION,
                postgres=replace(base.postgres, name=name),
            )


def rules_inputs(profile=DeploymentProfile.PRODUCTION):
    """The renderer's inputs, rebuilt from a rendering, for refusal tests."""
    configuration, compose, _ = rendering(profile)
    services = compose["services"]
    logins = {}
    for login in sorted(INVENTORY):
        logins.setdefault(holder(login), ())
        logins[holder(login)] += (login,)
    return configuration, services, logins


def test_the_rule_inputs_reproduce_the_rendered_rules():
    """The refusal tests below start from inputs that render cleanly."""
    configuration, services, logins = rules_inputs()
    assert {login for login, _ in source_rules(configuration, services, logins)} == (
        INVENTORY
    )


def test_a_login_without_a_source_or_an_unknown_login_is_refused():
    """The renderer refuses any difference from the closed login inventory."""
    configuration, services, logins = rules_inputs()
    missing = {k: v for k, v in logins.items() if k != "scheduler"}
    with pytest.raises(ConfigError, match="disagree"):
        render_hba(configuration, services, missing)
    unknown = dict(logins, worker=("pk_stewardship_worker", "pk_stewardship_x"))
    with pytest.raises(ConfigError, match="disagree"):
        render_hba(configuration, services, unknown)


def test_the_operator_is_refused_from_any_other_service():
    """Only database-provision may hold the operator over TCP."""
    configuration, services, logins = rules_inputs()
    moved = dict(logins, worker=("pk_stewardship_worker", OPERATOR))
    with pytest.raises(ConfigError, match="operator"):
        render_hba(configuration, services, moved)
    shared = dict(logins)
    shared["database-provision"] = (OPERATOR, "pk_stewardship_migration")
    with pytest.raises(ConfigError, match="operator"):
        render_hba(configuration, services, shared)


def test_shared_or_missing_addresses_and_misplaced_one_shots_are_refused():
    """Two clients on one address, a dynamic client, a one-shot on backend."""
    configuration, services, logins = rules_inputs()
    services = {name: dict(service) for name, service in services.items()}
    services["worker"] = dict(services["worker"], networks=services["web"]["networks"])
    with pytest.raises(ConfigError, match="share one source"):
        render_hba(configuration, services, logins)
    configuration, services, logins = rules_inputs()
    services = dict(services)
    services["scheduler"] = dict(services["scheduler"], networks={"backend": {}})
    with pytest.raises(ConfigError, match="fixed backend address"):
        render_hba(configuration, services, logins)
    configuration, services, logins = rules_inputs()
    services = dict(services)
    services["migration"] = dict(
        services["migration"],
        networks={"offline-migration": {}, "backend": {}},
    )
    with pytest.raises(ConfigError, match="only its own network"):
        render_hba(configuration, services, logins)


def test_the_seeder_source_exists_only_in_local():
    """Production refuses a seeder address; LOCAL refuses one that is taken."""
    configuration, services, logins = rules_inputs()
    with pytest.raises(ConfigError, match="local profile"):
        render_hba(configuration, services, logins, seeder="172.29.240.30")
    configuration, services, logins = rules_inputs(DeploymentProfile.LOCAL)
    taken = services["worker"]["networks"]["backend"]["ipv4_address"]
    with pytest.raises(ConfigError, match="share one source"):
        render_hba(configuration, services, logins, seeder=taken)


def test_a_backend_member_without_a_fixed_address_is_refused(monkeypatch):
    """The renderer refuses to render a backend member it has no address for."""
    from parishkit.stewardship import runtime_topology

    real = runtime_topology._backend_addresses

    def without_valkey(configuration, targets):
        """Every address but Valkey's."""
        return {k: v for k, v in real(configuration, targets).items() if k != "valkey"}

    monkeypatch.setattr(runtime_topology, "_backend_addresses", without_valkey)
    with pytest.raises(ConfigError, match="fixed address"):
        rendering(DeploymentProfile.PRODUCTION)


def test_the_offline_block_is_validated_and_recorded_only_when_set(tmp_path):
    """Private, canonical, /24 or /25, disjoint; absent from documents by default."""
    network = RuntimeNetwork()
    assert network.offline == DEFAULT_OFFLINE == "172.29.242.0/24"
    assert network_document(network) == {
        "backend": "172.29.240.0/24",
        "proxy": "172.29.241.0/24",
    }
    custom = parse_network({"offline": "10.9.8.0/25"})
    assert network_document(custom)["offline"] == "10.9.8.0/25"
    assert custom.offline_subnet("backup-worker") == ip_network("10.9.8.64/28")
    for invalid in (
        "172.29.240.0/24",
        "172.29.241.0/25",
        "172.29.242.0/26",
        "172.29.242.0/23",
        "8.8.8.0/24",
        "172.29.242.1/24",
        "fd00::/64",
    ):
        with pytest.raises(ConfigError):
            parse_network({"offline": invalid})
    with pytest.raises(ConfigError):
        network.offline_subnet("token-key-rotation")
    # A default deployment's record has no offline key, so the previous
    # release's loader, which refuses unknown keys, still reads it.
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path)})
    document = deployment_document(configuration)["deployment"]
    assert "offline" not in document["runtime_network"]
    chosen = replace(configuration, runtime_network=custom)
    document = deployment_document(chosen, switches=False)
    assert document["deployment"]["runtime_network"]["offline"] == "10.9.8.0/25"
    assert load_deployment(document=document, environ={}).runtime_network == custom


def test_the_inspection_login_has_a_socket_rule_and_no_tcp_rule():
    """The inspection login is socket-only: not in the inventory, so no TCP rule.

    ``database-sources`` probes each ``database_identities()`` login over TCP;
    keeping the inspection login out of that list keeps it from being tried.
    """
    from parishkit.stewardship.database_sources import inventory

    assert INSPECT_LOGIN not in {entry[1] for entry in database_identities()}
    assert INSPECT_LOGIN not in inventory()
    _, _, hba = rendering(DeploymentProfile.PRODUCTION)
    assert [line for line in hba.splitlines() if INSPECT_LOGIN in line] == [
        f'local "stewardship" {INSPECT_LOGIN} trust'
    ]
