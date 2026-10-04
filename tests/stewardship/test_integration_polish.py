"""Integration page polish (#199): conditional daily time, short-lived results."""

from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone

from parishkit.stewardship.accounts import integration_credentials as credentials
from parishkit.stewardship.accounts.integration_forms import IntegrationForm
from parishkit.stewardship.accounts.integration_views import _retain_unused_time


def test_refresh_times_are_marked_to_show_only_for_set_times():
    """The browser hides the time list unless the set-times frequency is chosen."""
    widget = IntegrationForm("parishsoft").fields["full_refresh_times"].widget
    assert widget.attrs["data-show-when"] == "full_refresh=daily"


def test_hidden_times_keep_the_stored_values():
    """An hourly refresh never turns an empty or stale time list into a change."""
    before = {"full_refresh": "daily", "nightly_time": "03:30"}
    settings = {
        "full_refresh": "hourly",
        "nightly_time": "02:00",
        "full_refresh_times": ["02:00"],
    }
    assert _retain_unused_time("parishsoft", settings, before) == {
        "full_refresh": "hourly",
        "nightly_time": "03:30",
        "full_refresh_times": ["03:30"],
    }
    before |= {"full_refresh_times": ["03:30", "15:00"]}
    assert _retain_unused_time("parishsoft", settings, before)[
        "full_refresh_times"
    ] == ["03:30", "15:00"]


def test_times_still_change_for_set_times():
    """Chosen times are kept when the refresh runs at set times."""
    settings = {
        "full_refresh": "daily",
        "nightly_time": "04:15",
        "full_refresh_times": ["04:15", "16:00"],
    }
    assert _retain_unused_time("parishsoft", settings, {}) == settings


def test_other_integrations_are_untouched():
    """Only ParishSoft has a refresh schedule."""
    settings = {"sender": "office@example.org"}
    assert _retain_unused_time("email", settings, {}) is settings


def test_finished_key_changes_stop_showing_after_an_hour():
    """A settled result is recent for an hour, then drops off the page."""
    now = timezone.now()
    recent = SimpleNamespace(updated_at=now - timedelta(minutes=50))
    old = SimpleNamespace(updated_at=now - timedelta(minutes=70))
    assert not credentials._settled_long_ago(recent)
    assert credentials._settled_long_ago(old)
