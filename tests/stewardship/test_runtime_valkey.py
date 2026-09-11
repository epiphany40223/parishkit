"""Web's credential cannot administer Valkey or access another service's queues."""

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.runtime_valkey import web_acl


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
