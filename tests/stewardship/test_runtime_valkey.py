"""Web's credential cannot administer Valkey or access another service's queues."""

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_valkey import broker_acl, server_acl, web_acl


def test_web_acl_contains_only_foundation_keys_and_commands():
    output = web_acl(b"synthetic-valkey-password").decode()
    assert "synthetic-valkey-password" not in output
    assert output.startswith("user default off\nuser web on #")
    assert "~stewardship:auth:v1:*" in output
    assert "~stewardship:ops:v1:*" in output
    assert "~*" not in output
    assert "+info" in output and "+get" in output
    assert {"+exists", "+del"} <= set(output.split())
    for forbidden in ("+@all", "+acl", "+config", "+flushall", "+flushdb", "+shutdown"):
        assert forbidden not in output


@pytest.mark.parametrize(
    "value", [None, "private", b"", b"a\nb", b"a b", b"\xff", b"a" * 257]
)
def test_valkey_acl_rejects_malformed_bytes_without_reflecting_them(value):
    with pytest.raises(ConfigError) as error:
        web_acl(value)
    assert "private" not in str(error.value)


@pytest.mark.parametrize(
    "role", [ServiceRole.WORKER, ServiceRole.MAIL_DISPATCH, ServiceRole.BACKUP_WORKER]
)
def test_consumer_acl_never_reaches_web_or_other_service_keys(role):
    """Restoration metadata and every command remain scoped to one consumer."""
    output = broker_acl(role, b"synthetic-private-password").decode()
    assert "synthetic-private-password" not in output
    assert f"~stewardship:broker:v1:{role.value}:unacked*" in output
    assert "stewardship:auth:" not in output and "~*" not in output
    if role is ServiceRole.WORKER:
        assert "mail-dispatch" not in output and "backup-worker" not in output
    assert "+brpop" in output and "+watch" in output
    for forbidden in (
        "+@all",
        "+acl",
        "+config",
        "+flushall",
        "+flushdb",
        "+publish",
        "+subscribe",
    ):
        assert forbidden not in output


def test_scheduler_acl_publishes_but_cannot_consume_or_delete():
    """Hint recovery grants publication, not business data or consumer authority."""
    output = broker_acl(ServiceRole.SCHEDULER, b"synthetic-password").decode()
    assert "+lpush" in output and "_kombu.binding.general" in output
    for command in ("+brpop", "+rpop", "+del", "+hget", "+eval", "+config"):
        assert command not in output


def test_server_acl_keeps_separate_passwords_and_disables_default():
    """The pure provisioning vocabulary grants no implicit additional identity."""
    output = server_acl(
        {ServiceRole.WEB: b"web-private", ServiceRole.WORKER: b"worker-private"}
    ).decode()
    assert output.count("user default off") == 1 and output.count("user ") == 3
    assert "private" not in output
    with pytest.raises(ConfigError):
        server_acl({ServiceRole.WORKER: b"worker-private"})
