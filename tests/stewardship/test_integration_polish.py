"""Integration page polish (#199): conditional daily time, short-lived results."""

from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone

from parishkit.stewardship.accounts import integration_credentials as credentials
from parishkit.stewardship.accounts.integration_forms import IntegrationForm
from parishkit.stewardship.accounts.integration_views import _retain_unused_time


def test_daily_time_is_marked_to_show_only_for_once_a_day():
    """The browser hides the time unless the once-a-day frequency is chosen."""
    widget = IntegrationForm("parishsoft").fields["nightly_time"].widget
    assert widget.attrs["data-show-when"] == "full_refresh=daily"


def test_hidden_daily_time_keeps_the_stored_value():
    """An hourly refresh never turns an empty or stale time into a change."""
    before = {"full_refresh": "daily", "nightly_time": "03:30"}
    settings = {"full_refresh": "hourly", "nightly_time": "02:00"}
    assert _retain_unused_time("parishsoft", settings, before) == {
        "full_refresh": "hourly",
        "nightly_time": "03:30",
    }


def test_daily_time_still_changes_for_once_a_day():
    """A chosen time is kept when the refresh runs once a day."""
    settings = {"full_refresh": "daily", "nightly_time": "04:15"}
    assert _retain_unused_time("parishsoft", settings, {}) == settings


def test_other_integrations_are_untouched():
    """Only ParishSoft has a refresh schedule."""
    settings = {"sender": "office@example.org"}
    assert _retain_unused_time("email", settings, {}) is settings


def test_finished_key_changes_stop_showing_after_a_day():
    """A settled result is recent for a day, then drops off the page."""
    now = timezone.now()
    recent = SimpleNamespace(updated_at=now - timedelta(hours=2))
    old = SimpleNamespace(updated_at=now - timedelta(days=2))
    assert not credentials._settled_long_ago(recent)
    assert credentials._settled_long_ago(old)
