"""Full-refresh status: a later success clears the failure banner and its link."""

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.source import refresh_status
from parishkit.stewardship.source.refresh_status import FullRefreshStatus

EARLY = datetime(2026, 9, 28, 6, 3, tzinfo=UTC)
LATE = datetime(2026, 9, 28, 11, 15, tzinfo=UTC)


def status_from(monkeypatch, row):
    """Run full_refresh_status against one canned database row."""

    class Cursor:
        def execute(self, sql):
            self.sql = sql

        def fetchone(self):
            return row

    class Connection:
        @contextmanager
        def cursor(self):
            yield Cursor()

    monkeypatch.setattr(refresh_status, "connection", Connection())
    return refresh_status.full_refresh_status()


def test_later_success_clears_the_failure_and_its_link(monkeypatch):
    """The banner disappears once a newer full load succeeds."""
    status = status_from(monkeypatch, (LATE, EARLY, False, uuid4()))
    assert status.failed_at is None and status.failed_task_id is None


def test_newer_failure_keeps_its_task_link(monkeypatch):
    """A failure after the last success is shown with its task."""
    task = uuid4()
    status = status_from(monkeypatch, (EARLY, LATE, False, task))
    assert status.failed_at == LATE and status.failed_task_id == task


def test_banner_links_to_the_failed_task_not_the_background_list():
    """Admins land on the failing run's details, not the generic list."""
    task = uuid4()
    html = render_to_string(
        "stewardship/full-refresh-status.html",
        {"status": FullRefreshStatus(EARLY, LATE, False, task), "can_view_task": True},
    )
    assert f"/admin/background/task/{task}" in html
    assert "See Background work" not in html


def test_banner_omits_the_link_for_users_who_cannot_open_tasks():
    """Staff see the failure notice but no link to a page they cannot open."""
    task = uuid4()
    html = render_to_string(
        "stewardship/full-refresh-status.html",
        {"status": FullRefreshStatus(EARLY, LATE, False, task), "can_view_task": False},
    )
    assert "notice-error" in html
    assert f"/admin/background/task/{task}" not in html


def test_banner_absent_without_a_failure():
    """No failure means no error notice at all."""
    html = render_to_string(
        "stewardship/full-refresh-status.html",
        {"status": FullRefreshStatus(LATE, None, False)},
    )
    assert "notice-error" not in html
