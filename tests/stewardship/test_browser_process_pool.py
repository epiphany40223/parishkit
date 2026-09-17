"""Our fixture reuses startup while preserving isolation and the WebKit fix."""

from unittest.mock import Mock

import pytest

from .browser.process_pool import BrowserProcesses


def factory():
    """Model resource ownership only; real UI assertions remain browser tests."""
    runner = Mock()
    runner.chromium.launch.return_value.contexts = []
    runner.firefox.launch.return_value.contexts = []
    manager = Mock()
    manager.start.return_value = runner
    manager.__enter__ = Mock(return_value=runner)
    manager.__exit__ = Mock(return_value=False)
    return Mock(return_value=manager), runner


def test_repeated_scenarios_share_only_process_startup():
    create, runner = factory()
    pool = BrowserProcesses(create)
    for _ in range(3):
        with pool.acquire("firefox") as browser:
            assert browser is runner.firefox.launch.return_value
    with pool.acquire("chromium"):
        pass
    create.assert_called_once_with()
    runner.firefox.launch.assert_called_once_with()
    runner.chromium.launch.assert_called_once_with()
    pool.close()
    runner.firefox.launch.return_value.close.assert_called_once_with()
    runner.chromium.launch.return_value.close.assert_called_once_with()
    runner.stop.assert_called_once_with()
    pool.close()
    runner.stop.assert_called_once_with()


def test_webkit_retains_fresh_driver_and_browser_each_time():
    create, runner = factory()
    pool = BrowserProcesses(create)
    with pool.acquire("firefox"):
        pass
    for _ in range(2):
        with pool.acquire("webkit"):
            assert pool.runner is None and not pool.browsers
    assert create.call_count == 3
    assert runner.webkit.launch.call_count == 2
    assert runner.webkit.launch.return_value.close.call_count == 2
    runner.stop.assert_called_once_with()


def test_leaked_context_fails_and_is_closed_before_next_scenario():
    create, runner = factory()
    pool = BrowserProcesses(create)
    leaked = Mock()
    with (
        pytest.raises(AssertionError, match="leaked"),
        pool.acquire("firefox") as browser,
    ):
        browser.contexts = [leaked]
    leaked.close.assert_called_once_with()
    pool.close()


def test_unknown_engine_does_not_start_tooling():
    create, _ = factory()
    with pytest.raises(ValueError), BrowserProcesses(create).acquire("unknown"):
        pytest.fail("Unknown browser was admitted")
    create.assert_not_called()
