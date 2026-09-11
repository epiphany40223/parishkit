"""No caller can use a nominal counter field to retain raw private values."""

from unittest.mock import Mock

import pytest
from django.db import DatabaseError
from redis.exceptions import RedisError

from parishkit.stewardship.accounts.limiting import Counter, Limiter, LocalBuckets


@pytest.mark.parametrize(
    "name",
    [
        "admin_start",
        "admin_callback",
        "admin_identity_1",
        "admin_identity_99",
        "family_ip",
        "family_pair",
    ],
)
def test_counter_closed_names_include_policy_epoch(name):
    assert Counter(name, "f" * 64, 5, 900).name == name


@pytest.mark.parametrize(
    "values",
    [
        ("private@example.org", "f" * 64, 5, 900),
        ("admin_identity_private", "f" * 64, 5, 900),
        ("admin_identity_0", "f" * 64, 5, 900),
        ("family_pair", "SECRET", 5, 900),
        ("family_pair", "f" * 64, True, 900),
        ("family_pair", "f" * 64, 0, 900),
        ("family_pair", "f" * 64, 10001, 900),
        ("family_pair", "f" * 64, 5, 86401),
    ],
)
def test_counter_bad_inputs_rejected_without_echoing(values):
    with pytest.raises(ValueError, match="counter policy") as raised:
        Counter(*values)
    assert all(
        value not in str(raised.value) for value in values if isinstance(value, str)
    )


def test_local_token_buckets_have_fixed_memory_and_refill():
    """The bounded outage fallback is tested without either backing service."""
    now = [0]
    buckets = LocalBuckets(capacity=2, clock=lambda: now[0])
    for _ in range(30):
        assert buckets.consume("one") == 0
    assert buckets.consume("one") == 1
    assert buckets.consume("two") == 0
    assert buckets.consume("three") == 60
    now[0] += 1
    assert buckets.consume("one") == 0
    now[0] += 121
    assert buckets.consume("three") == 0
    assert len(buckets.entries) == 1


def test_post_login_counter_cleanup_cannot_fail_the_committed_login():
    """Even simultaneous broker/incident outages leave post-commit success intact."""
    client = Mock()
    client.delete.side_effect = RedisError("synthetic broker failure")
    incident = Mock(side_effect=DatabaseError("synthetic incident failure"))
    limiter = Limiter(client, b"s" * 32, incident=incident)
    limiter.clear(Counter("family_pair", "f" * 64, 5, 900))
    assert limiter.outage is True
    incident.assert_called_once()
