"""Operational PostgreSQL cannot silently negotiate an unverified remote link."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import database_provisioning, runtime_database

from .test_runtime_topology import configuration_at


@pytest.mark.parametrize("host,port", [("external.example", 5432), ("postgres", 55432)])
def test_external_sql_rejected_before_reading_secrets_or_connecting(
    tmp_path, monkeypatch, host, port
):
    """Both runtime and offline provisioning enforce the renderer's local profile."""
    configuration = configuration_at(tmp_path)
    configuration = replace(
        configuration, postgres=replace(configuration.postgres, host=host, port=port)
    )
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(database_provisioning.psycopg, "connect", connect)
    with pytest.raises(ConfigError, match="private Compose"):
        runtime_database.database_settings(configuration)
    with pytest.raises(ConfigError, match="private Compose"):
        database_provisioning._connection(configuration, "operator", b"synthetic")
    connect.assert_not_called()
