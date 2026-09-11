"""Only explicit private bridges and one exact proxy peer define forwarded trust."""

from dataclasses import replace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import load_deployment
from parishkit.stewardship.deployment_documents import deployment_document
from parishkit.stewardship.runtime_network import RuntimeNetwork, parse_network


def test_default_runtime_network_is_disjoint_and_predictable():
    """The database is never on the proxy bridge and peer trust is one address."""
    network = RuntimeNetwork()
    assert network.caddy == "172.29.241.2"
    assert network.web() == "172.29.241.10"
    assert network.web(7) == "172.29.241.17"
    assert parse_network({}) == network
    for invalid in (-1, 8, True):
        with pytest.raises(ConfigError):
            network.web(invalid)


@pytest.mark.parametrize(
    "values",
    [
        {"backend": "172.29.241.0/24"},
        {"proxy": "172.29.240.0/25"},
        {"proxy": "127.0.0.0/24"},
        {"proxy": "8.8.8.0/24"},
        {"proxy": "fd00::/64"},
        {"proxy": "10.0.0.1/24"},
        {"backend": "10.0.0.0/8"},
        {"proxy": "private-input"},
        {"proxy": True},
        {"unknown": "private-input"},
        None,
    ],
)
def test_invalid_ambiguous_public_or_broad_topology_is_rejected(values):
    """Invalid network values do not become diagnostics or forwarded trust rules."""
    with pytest.raises(ConfigError) as error:
        parse_network(values)
    assert "private-input" not in str(error.value)


def test_runtime_documents_round_trip_all_nonsecret_overrides(tmp_path):
    """Operator/runtime YAML rendering preserves the validated deployment exactly."""
    import json

    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path)})
    configuration = replace(
        configuration,
        postgres=replace(
            configuration.postgres,
            password_file=tmp_path / "database",
            download_password_file=tmp_path / "downloads",
        ),
        runtime_network=RuntimeNetwork(backend="10.42.0.0/24", proxy="10.42.1.0/24"),
    )
    path = tmp_path / "deployment.yaml"
    path.write_text(json.dumps(deployment_document(configuration)))
    assert load_deployment(path, environ={}) == replace(
        configuration, configuration_file=path
    )
