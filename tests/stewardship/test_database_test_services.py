"""Test-only local shard selection cannot point authentication at a remote host."""

import pytest

from .database import auth_builders


@pytest.mark.parametrize("configured,expected", [(None, 56379), ("56381", 56381)])
def test_valkey_shard_port_preserves_loopback_and_bounded_timeouts(
    monkeypatch, configured, expected
):
    """The fixture changes only its explicitly configured disposable port."""
    monkeypatch.delenv("PARISHKIT_TEST_VALKEY_PORT", raising=False)
    if configured is not None:
        monkeypatch.setenv("PARISHKIT_TEST_VALKEY_PORT", configured)
    monkeypatch.setattr(auth_builders, "Redis", lambda **kwargs: kwargs)
    assert auth_builders.valkey_client() == {
        "host": "127.0.0.1",
        "port": expected,
        "socket_timeout": 2,
        "socket_connect_timeout": 2,
    }


@pytest.mark.parametrize("port", ["", "not-a-port", "-1", "0", "80", "56380", "65536"])
def test_invalid_valkey_test_port_fails_before_creating_client(monkeypatch, port):
    """The suite's unavailable-service port remains deliberately unused."""
    monkeypatch.setenv("PARISHKIT_TEST_VALKEY_PORT", port)

    def forbidden(**kwargs):
        """Invalid configuration must not construct or connect any client."""
        pytest.fail("Unexpected client construction")

    monkeypatch.setattr(auth_builders, "Redis", forbidden)
    with pytest.raises(ValueError):
        auth_builders.valkey_client()
