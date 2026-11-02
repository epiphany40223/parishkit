"""Admission budgets include rollouts and reserve non-download work capacity."""

from dataclasses import replace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_budget import RuntimeBudget, parse_budget


def test_default_budget_accounts_for_all_classes_and_overlap():
    budget = RuntimeBudget()
    assert budget.total_connections == 92
    assert parse_budget({}) == budget
    budget.validate_database(maximum=100, reserved=3)
    budget.validate_database(maximum=200, reserved=2)
    with pytest.raises(ConfigError):
        replace(budget, replicas=3)
    budget.validate_topology(background_processes=13)
    with pytest.raises(ConfigError):
        budget.validate_topology(background_processes=19)


def test_total_is_the_sum_of_every_login_limit():
    """#2333: the total adds up exactly the limits provisioning sets.

    Each login's limit is what its service can open at once: the web
    processes' threads and observers plus the spare connections for pk-admin
    and the load check, the download pools, two consumer processes of three
    connections each, a loop plus its timeout log, and Django plus the
    timeout log for a one-shot. The backup login holds a running backup's
    Django and pg_dump, a waiting scheduled run, a request-mode poll and a
    timeout log at once. The inspection login counts too.
    """
    from types import SimpleNamespace

    from parishkit.stewardship.database_provisioning import (
        expected_limits,
        role_limit,
    )
    from parishkit.stewardship.inspect_login import INSPECT_LOGIN
    from parishkit.stewardship.runtime_grants import login_name
    from parishkit.stewardship.runtime_identities import database_identities

    budget = RuntimeBudget()
    config = SimpleNamespace(runtime_budget=budget)
    limits = expected_limits(config)
    expected = {
        login_name(ServiceRole.WEB): 32,
        login_name("download"): 4,
        login_name(ServiceRole.WORKER): 6,
        login_name(ServiceRole.MAIL_DISPATCH): 6,
        login_name(ServiceRole.SCHEDULER): 2,
        login_name(ServiceRole.CONFIG_INSTALLER): 2,
        "pk_stewardship_migration": 2,
        login_name(ServiceRole.BOOTSTRAP): 2,
        login_name(ServiceRole.ADMIN_RECOVERY): 2,
        login_name(ServiceRole.BACKUP_WORKER): 5,
        login_name(ServiceRole.BACKUP_UPLOADER): 2,
        INSPECT_LOGIN: 3,
    }
    assert {login: limits[login] for login in expected} == expected
    installers = [
        role_limit(config, role)
        for _, _, role, _ in database_identities()
        if role is ServiceRole.CREDENTIAL_INSTALLER
    ]
    assert installers == [2] * 12
    assert sum(limits.values()) == budget.total_connections == 92
    # Five ordinary slots stay free of every login, so the operator has
    # eight (with the three superuser-reserved ones) when all are at limit:
    # exactly operator_connections, the second rule's edge.
    assert budget.database_connections - budget.database_reserved - 92 == 5
    assert 92 + budget.operator_connections == budget.database_connections


@pytest.mark.parametrize(
    "changes",
    [
        # Rule 2 only: 92 + the operator's 8 > 99 (92 <= 99 - 3).
        {"database_connections": 99},
        # Rule 1 only: 92 > 100 - 9 superuser-reserved slots (92 + 8 = 100).
        {"database_reserved": 9},
        # Rule 2 only: 92 + 9 > 100 (92 <= 97).
        {"operator_connections": 9},
        # Rule 2 only: 85 + 16 > 100 (85 <= 97).
        {"web_spare_connections": 3, "operator_connections": 16},
        # Both rules: web 4 x 15 + 10 = 70, so 130 in all.
        {"web_threads": 12},
    ],
)
def test_budget_that_does_not_fit_is_refused(changes):
    with pytest.raises(ConfigError):
        parse_budget(changes)


def test_budget_that_just_fits_is_accepted():
    """The two rules' exact edges: 92 + 8 = 100 and 92 = 100 - 8."""
    assert parse_budget({"operator_connections": 8}).total_connections == 92
    assert parse_budget({"database_reserved": 8}).total_connections == 92
    edge = {"web_spare_connections": 3, "operator_connections": 15}
    assert parse_budget(edge).total_connections == 85


def test_background_inventory_includes_worker_renewal_and_rollout_overlap():
    """The SQL role ceilings consume only their reserved background connection pool."""
    from parishkit.stewardship.database_provisioning import role_limit
    from parishkit.stewardship.deployment import ServiceRole, load_deployment
    from parishkit.stewardship.runtime_identities import database_identities

    config = load_deployment(environ={})
    roles = {
        ServiceRole.WORKER,
        ServiceRole.MAIL_DISPATCH,
        ServiceRole.SCHEDULER,
        ServiceRole.CONFIG_INSTALLER,
        ServiceRole.CREDENTIAL_INSTALLER,
    }
    used = sum(
        role_limit(config, role)
        for _, _, role, _ in database_identities()
        if role in roles
    )
    # The worker's and mail dispatch's third slot per overlap (each container
    # runs two consumer processes: #336 and the two mail consumers) are the
    # only ceilings beyond the reserved pool. Compose never overlaps two
    # generations of a background service, so steady-state use is one
    # generation: 26 of the 36 reserved slots.
    overlap = config.runtime_budget.rollout_overlap
    assert used == config.runtime_budget.background_connections + 2 * overlap == 40
    steady = sum(
        role_limit(config, role)
        if role in {ServiceRole.WORKER, ServiceRole.MAIL_DISPATCH}
        else role_limit(config, role) // overlap
        for _, _, role, _ in database_identities()
        if role in roles
    )
    assert steady == 26 <= config.runtime_budget.background_connections
    assert role_limit(config, ServiceRole.WORKER) == 6
    assert role_limit(config, ServiceRole.MAIL_DISPATCH) == 6
    assert role_limit(config, ServiceRole.SCHEDULER) == 2
    doubled = replace(
        config, runtime_budget=replace(config.runtime_budget, rollout_overlap=1)
    )
    assert role_limit(doubled, ServiceRole.WORKER) == 3
    assert role_limit(doubled, ServiceRole.MAIL_DISPATCH) == 3


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
        # A download must fit the web stop's grace, the drain less 15 s.
        {
            "download_seconds": 300,
            "download_idle_seconds": 305,
            "drain_seconds": 315,
        },
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
