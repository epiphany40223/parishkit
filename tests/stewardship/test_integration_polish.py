"""Integration page polish (#199): short-lived key-change results."""

from datetime import timedelta
from types import SimpleNamespace

from django.utils import timezone

from parishkit.stewardship.accounts import integration_credentials as credentials


def test_finished_key_changes_stop_showing_after_an_hour():
    """A settled result is recent for an hour, then drops off the page."""
    now = timezone.now()
    recent = SimpleNamespace(updated_at=now - timedelta(minutes=50))
    old = SimpleNamespace(updated_at=now - timedelta(minutes=70))
    assert not credentials._settled_long_ago(recent)
    assert credentials._settled_long_ago(old)
