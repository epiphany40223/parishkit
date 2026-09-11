"""Admission budgets include rollouts and reserve non-download work capacity."""

from dataclasses import replace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.runtime_budget import RuntimeBudget, parse_budget


def test_default_budget_accounts_for_all_classes_and_overlap():
    budget = RuntimeBudget()
    assert budget.total_connections == 91
    assert parse_budget({}) == budget
    budget.validate_database(maximum=100, reserved=3)
    budget.validate_database(maximum=200, reserved=2)
    with pytest.raises(ConfigError):
        replace(budget, replicas=3)
    budget.validate_topology(background_processes=13)
    with pytest.raises(ConfigError):
        budget.validate_topology(background_processes=17)


def test_health_connections_are_already_in_the_auxiliary_reserve():
    """The web SQL ceiling's observation threads are counted once, not twice."""
    from types import SimpleNamespace

    from parishkit.stewardship.database_provisioning import role_limit
    from parishkit.stewardship.deployment import ServiceRole

    budget = RuntimeBudget(download_pool_per_process=4, download_capacity=8)
    config = SimpleNamespace(runtime_budget=budget)
    assert (
        budget.auxiliary_connections
        == 2 * budget.web_processes * budget.replicas * budget.rollout_overlap
    )
    assert (
        budget.total_connections
        == (
            role_limit(config, ServiceRole.WEB)
            + role_limit(config, "download")
            + budget.background_connections
            + budget.operator_connections
            + budget.database_reserved
        )
        == 99
    )
    with pytest.raises(ConfigError):
        replace(budget, auxiliary_connections=7)


@pytest.mark.parametrize(
    "changes",
    [
        {"web_threads": True},
        {"replicas": 0},
        {"database_connections": 10001},
        {"download_capacity": 33},
        {"download_pool_per_process": 5},
        {"web_threads": 2},
        {"download_capacity": 16},
        {"operator_connections": 1},
        {"background_connections": 1},
        {"database_connections": 78},
        {"download_idle_seconds": 300},
        {"drain_seconds": 330},
        {"server_timeout_seconds": 360},
        {"proxy_timeout_seconds": 369},
        {"download_seconds": 5},
        {
            "download_seconds": 4,
            "download_idle_seconds": 5,
            "drain_seconds": 6,
            "server_timeout_seconds": 7,
            "proxy_timeout_seconds": 8,
        },
        {
            "download_seconds": 30,
            "download_idle_seconds": 40,
            "drain_seconds": 60,
            "server_timeout_seconds": 70,
            "proxy_timeout_seconds": 80,
        },
    ],
)
def test_inconsistent_budgets_fail_before_service_start(changes):
    with pytest.raises(ConfigError):
        parse_budget(changes)


@pytest.mark.parametrize("value", [None, [], {"password": "private"}])
def test_unknown_shapes_do_not_echo_input(value):
    with pytest.raises(ConfigError) as error:
        parse_budget(value)
    assert "private" not in str(error.value)


@pytest.mark.parametrize("maximum,reserved", [(99, 3), (100, 4), (100, -1), (True, 3)])
def test_live_database_limits_must_match(maximum, reserved):
    with pytest.raises(ConfigError):
        RuntimeBudget().validate_database(maximum=maximum, reserved=reserved)
