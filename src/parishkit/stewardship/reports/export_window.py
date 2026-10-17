"""The point-in-time export window (#1398; operations spec, "Point-in-time exports").

A finished export file can be downloaded, as often as needed, for one window
after it is published; a failed export can be retried for one window after
its latest failed attempt. The window is the deployment's
``export_download_window_minutes`` (default 60, 15 to 240), which runtime
assembly copies into ``settings.STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES``.

Each end is counted from its own start with the *current* setting, so a
shorter setting applies at once. A publication also records the end it was
published with (``expires_at``), and its window ends at the earlier of the
two, so a longer setting never extends a file, and an older file's seven-day
``expires_at`` no longer keeps it.
"""

from datetime import timedelta

DEFAULT_MINUTES = 60
MINIMUM_MINUTES = 15
MAXIMUM_MINUTES = 240


def window():
    """The current window as a ``timedelta`` (the default outside a runtime)."""
    from django.conf import settings

    minutes = getattr(
        settings, "STEWARDSHIP_EXPORT_DOWNLOAD_WINDOW_MINUTES", DEFAULT_MINUTES
    )
    return timedelta(minutes=minutes)


def download_end(publication):
    """When ``publication``'s file stops being downloadable."""
    return min(publication.expires_at, publication.created_at + window())


def retry_end(failed_at):
    """When a failed export, whose latest attempt failed at ``failed_at``,
    stops offering Retry."""
    return failed_at + window()
