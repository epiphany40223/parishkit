"""The startup database wait reads a real server's replies correctly (#453).

libpq reports a failed connection without a SQLSTATE, so runtime_database
tells "not yet" from "refused" by the server's reply. These tests pin that
against the pinned PostgreSQL image: the service's login connects, and a
wrong password or a missing database is refused at once, never waited out.
"""

import pytest
from django.conf import settings
from django.db.utils import OperationalError

from parishkit.stewardship import runtime_database

from ..test_runtime_topology import configuration_at


@pytest.fixture(autouse=True)
def _no_startup_budget(monkeypatch):
    """Leave no startup window behind for later admission tests."""
    monkeypatch.setattr(runtime_database, "_startup_started", None)


def _settings(**changes):
    """The disposable server's connection, in database_settings' shape."""
    database = settings.DATABASES["default"]
    return {
        "HOST": database["HOST"],
        "PORT": database["PORT"],
        "NAME": database["NAME"],
        "USER": database["USER"],
        "PASSWORD": database["PASSWORD"],
        "OPTIONS": {"connect_timeout": 5, "sslmode": "disable"},
    } | changes


def _never_sleep(seconds):
    """A refusal or a ready server must not wait at all."""
    raise AssertionError("the startup wait slept")


def test_a_ready_server_is_used_at_once(tmp_path, monkeypatch):
    """The service's own login connects and answers SELECT 1 on the first try."""
    monkeypatch.setattr(
        runtime_database, "database_settings", lambda value: _settings()
    )
    monkeypatch.setattr(runtime_database.time, "sleep", _never_sleep)
    runtime_database.await_database(configuration_at(tmp_path))


@pytest.mark.parametrize(
    "changes",
    [{"PASSWORD": "not-the-disposable-password"}, {"NAME": "no_such_database"}],
)
def test_a_real_refusal_is_not_waited_out(tmp_path, monkeypatch, changes):
    """A wrong password or a missing database is refused on the first reply."""
    monkeypatch.setattr(
        runtime_database, "database_settings", lambda value: _settings(**changes)
    )
    monkeypatch.setattr(runtime_database.time, "sleep", _never_sleep)
    with pytest.raises(OperationalError, match="refused this service"):
        runtime_database.await_database(configuration_at(tmp_path))
