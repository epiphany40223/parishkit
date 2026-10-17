"""The point-in-time export window (#1398; reports.export_window)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from parishkit.stewardship.reports import export_window

PUBLISHED = datetime(2054, 10, 5, 14, 0, tzinfo=UTC)


def publication(expires_after):
    """A publication made at PUBLISHED that recorded ``expires_after``."""
    return SimpleNamespace(created_at=PUBLISHED, expires_at=PUBLISHED + expires_after)


def test_the_window_is_the_setting_with_a_60_minute_default(settings):
    """Outside a runtime the base settings' 60 minutes apply."""
    assert export_window.window() == timedelta(minutes=60)
    settings.STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES = 15
    assert export_window.window() == timedelta(minutes=15)


def test_a_file_ends_at_the_earlier_of_its_record_and_the_setting(settings):
    """A shorter setting applies at once; a longer one never extends a file;
    an older file's seven-day record no longer keeps it."""
    settings.STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES = 60
    hour = PUBLISHED + timedelta(minutes=60)
    assert export_window.download_end(publication(timedelta(minutes=60))) == hour
    assert export_window.download_end(publication(timedelta(days=7))) == hour
    settings.STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES = 15
    quarter = PUBLISHED + timedelta(minutes=15)
    assert export_window.download_end(publication(timedelta(minutes=60))) == quarter
    settings.STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES = 240
    assert export_window.download_end(publication(timedelta(minutes=60))) == hour


def test_a_failed_export_can_be_retried_for_one_window(settings):
    """Its Retry ends one window, the current setting, after it failed."""
    settings.STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES = 30
    assert export_window.retry_end(PUBLISHED) == PUBLISHED + timedelta(minutes=30)


def test_an_export_ends_by_its_publication_cancellation_or_failed_task(monkeypatch):
    """``export_ended``: the file's window, a cancellation, or a failed run
    not retried within one window; a pending or retryable one has not."""
    from parishkit.stewardship.reports import export_services

    now = PUBLISHED + timedelta(minutes=61)
    monkeypatch.setattr(export_services, "database_now", lambda: now)

    def rows(found):
        """A stand-in manager whose filter(...).first() / exists() give ``found``."""
        query = SimpleNamespace(first=lambda: found, exists=lambda: found is not None)
        return SimpleNamespace(objects=SimpleNamespace(filter=lambda **kw: query))

    def task(state, failed_at):
        """A task whose latest run is in ``state``, last changed at ``failed_at``."""
        latest = SimpleNamespace(state=state, updated_at=failed_at)
        ordered = SimpleNamespace(first=lambda: latest)
        return SimpleNamespace(chain_runs=SimpleNamespace(order_by=lambda *a: ordered))

    def ended(*, published=None, cancelled=False, run=("queued", now)):
        """``export_ended`` for an export in the given situation."""
        monkeypatch.setattr(export_services, "ExportPublication", rows(published))
        monkeypatch.setattr(
            export_services, "ExportCancellation", rows(object() if cancelled else None)
        )
        return export_services.export_ended(SimpleNamespace(task=task(*run)))

    assert ended(published=publication(timedelta(minutes=60)))
    recent = SimpleNamespace(
        created_at=now - timedelta(minutes=30), expires_at=now + timedelta(minutes=30)
    )
    assert not ended(published=recent)
    assert ended(cancelled=True)
    assert not ended()
    assert not ended(run=("failed", now - timedelta(minutes=59)))
    assert ended(run=("failed", now - timedelta(minutes=60)))
    assert not ended(run=("abandoned", now - timedelta(days=1)))
