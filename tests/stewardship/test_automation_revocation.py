"""``pk-stewardship revoke-automation-sessions``: options and refusals (ADM-11).

The offline command that ends every live Admin automation session in a
restore takes only ``--config`` and ``--reason``, and refuses any reason but
``restore`` and ``revoked_by_operator`` before any admission. Its database
effect is proven against PostgreSQL in
database/test_automation_logins_postgresql.py.
"""

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import cli


def test_revoke_automation_sessions_accepts_only_its_options(monkeypatch):
    """--config and --reason, nothing else; it runs as an operator command."""
    calls = []
    monkeypatch.setattr(
        "parishkit.stewardship.operator_commands.execute_operator",
        lambda args: calls.append(args) or 0,
    )
    assert (
        cli.main(["revoke-automation-sessions", "--config", "x", "--reason", "restore"])
        == 0
    )
    assert calls[0].reason == "restore"
    with pytest.raises(SystemExit):
        cli.main(["revoke-automation-sessions", "--config", "x", "--email", "a"])


def test_offline_revocation_refuses_other_reasons():
    """Only restore and revoked_by_operator, checked before any admission."""
    from parishkit.stewardship.operator_commands import revoke_automation_command

    with pytest.raises(ConfigError):
        revoke_automation_command(object(), reason="logout")
