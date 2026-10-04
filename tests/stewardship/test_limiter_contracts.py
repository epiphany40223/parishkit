"""No caller can use a nominal counter field to retain raw private values."""

from ipaddress import IPv4Address, IPv6Address
from unittest.mock import Mock

import pytest
from django.db import DatabaseError
from redis.exceptions import RedisError

from parishkit.stewardship.accounts.limiting import (
    Counter,
    Limiter,
    LocalBuckets,
    source_network,
)


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


@pytest.mark.parametrize(
    ("address", "network"),
    [
        (IPv4Address("192.0.2.7"), "192.0.2.7"),
        ("192.0.2.7", "192.0.2.7"),
        (IPv6Address("2001:db8:1:2:aaaa:bbbb:cccc:dddd"), "2001:db8:1:2::/64"),
        ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
        ("2001:DB8:0001:0002:0:0:0:FFFF", "2001:db8:1:2::/64"),
        ("fe80::1%eth0", "fe80::/64"),
        (IPv6Address("::ffff:192.0.2.7"), "192.0.2.7"),
        ("::1", "::/64"),
    ],
)
def test_source_network_groups_ipv6_by_64_and_keeps_ipv4_exact(address, network):
    """#383: one IPv6 end site (a /64) is one limiter source; IPv4 is exact."""
    assert source_network(address) == network


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-an-address",
        "2001:db8::/64",
        None,
        3221225985,
        b"\xc0\x00\x02\x01",
        True,
    ],
)
def test_source_network_refuses_anything_but_an_address(value):
    """A malformed source fails closed instead of becoming its own budget."""
    with pytest.raises(ValueError):
        source_network(value)


def test_source_fingerprints_share_a_64_and_separate_others():
    """Every per-source limit keys on the fingerprint, so this is the grouping."""
    limiter = Limiter(Mock(), b"s" * 32, incident=Mock())
    one = limiter.fingerprint("ip", IPv6Address("2001:db8:1:2::1"))
    assert one == limiter.fingerprint("ip", IPv6Address("2001:db8:1:2:ffff::9"))
    assert one == limiter.fingerprint("ip", "2001:db8:1:2::1234")
    assert one != limiter.fingerprint("ip", IPv6Address("2001:db8:1:3::1"))
    mapped = limiter.fingerprint("ip", IPv6Address("::ffff:192.0.2.7"))
    assert mapped == limiter.fingerprint("ip", IPv4Address("192.0.2.7"))
    assert mapped != limiter.fingerprint("ip", IPv4Address("192.0.2.8"))
    # Domain separation still holds, and only the "ip" kind is grouped.
    assert one != limiter.fingerprint("pair", "2001:db8:1:2::/64")
    assert limiter.fingerprint(
        "pair", IPv6Address("2001:db8:1:2::1")
    ) == limiter.fingerprint("pair", "2001:db8:1:2::1")
    assert limiter.fingerprint(
        "pair", IPv6Address("2001:db8:1:2::1")
    ) != limiter.fingerprint("pair", IPv6Address("2001:db8:1:2::2"))
