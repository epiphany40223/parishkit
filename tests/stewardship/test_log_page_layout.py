"""System logs layout: level icons, compact filters, cross-links, hidden IDs."""

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.policy import Capability, Principal, allows
from parishkit.stewardship.audit.log_rows import (
    LEVELS,
    LogQuery,
    audit_row,
    operational_row,
    page_context,
    task_subject,
)

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _operational(level, index):
    """One operational row as the view hands it to the template."""
    row = operational_row(
        {
            "id": UUID(int=100 + index),
            "created_at": NOW,
            "level": level,
            "event": "task_failed",
            "actor_id": UUID(int=900),
            "correlation_id": UUID(int=200 + index),
            "context": {"outcome": "failed"},
        }
    )
    row.update(actor=None, actor_worker=True, task_subject=False)
    return row


def _audit():
    """A task audit entry: its subject is the task, so it links to the task page."""
    row = audit_row(
        {
            "id": UUID(int=300),
            "created_at": NOW,
            "event_type": "task_retry_requested",
            "actor_id": UUID(int=301),
            "correlation_id": UUID(int=302),
            "campaign_reference": UUID(int=303),
            "subject_id": UUID(int=304),
            "auditcontext__context": None,
        }
    )
    row.update(actor="admin@example.org", actor_worker=False, task_subject=True)
    return row


def _render(query=None, rows=None):
    """Render the page from the production context builder."""
    rows = (
        rows
        if rows is not None
        else [
            *(_operational(level, index) for index, level in enumerate(LEVELS)),
            _audit(),
        ]
    )
    return render_to_string(
        "stewardship/logs.html", page_context(query or LogQuery(), rows, None)
    )


@pytest.mark.parametrize("level", [level.lower() for level in LEVELS])
def test_each_level_has_its_own_decorative_icon(level):
    """One matched icon per level, hidden from assistive technology."""
    html = _render()
    icon = re.search(rf'<svg class="level-icon level-icon-{level}"[^>]*>', html)
    assert icon and 'aria-hidden="true"' in icon.group(0)
    assert 'focusable="false"' in icon.group(0)
    assert "style=" not in html


def test_critical_rows_stand_out():
    """A CRITICAL entry's row is highlighted; the others are not."""
    html = _render()
    assert html.count('class="log-row-critical"') == 1


def test_identifiers_sit_under_closed_technical_details():
    """Every identifier shown as text is inside a closed Technical details block.

    Identifiers still travel in hidden inputs and links, which are not text.
    """
    html = _render()
    assert html.count('<details class="technical-details">') == len(LEVELS) + 1
    visible = re.sub(
        r"<details class=\"technical-details\">.*?</details>", "", html, flags=re.S
    )
    visible = re.sub(r"<[^>]+>", " ", visible)
    assert not UUID_TEXT.search(visible)


def test_entries_cross_link_by_correlation_actor_campaign_and_task():
    """IDs become actions: related entries, same actor, same campaign, open task."""
    html = _render()
    rows = len(LEVELS) + 1
    assert html.count(">Show related entries</button>") == rows
    assert html.count(">Same actor</button>") == rows
    assert html.count(">Same campaign</button>") == 1
    assert html.count(">Open task</a>") == 1
    assert f'href="/admin/background/task/{UUID(int=304)}">Open task</a>' in html
    # A campaign filter only ever matches audit records, so it asks for them.
    assert re.search(
        r'name="source" value="audit"><input type="hidden" name="campaign"', html
    )


def test_identifier_filters_fold_away_unless_used():
    """The three identifier filters are closed until one of them is set."""
    closed = _render()
    assert '<details class="log-more-filters">' in closed
    used = _render(LogQuery.parse({"applied": "yes", "actor": str(UUID(int=5))}))
    assert '<details class="log-more-filters" open>' in used


@pytest.mark.parametrize(
    ("event", "subject", "linked"),
    [
        ("task_retry_requested", UUID(int=1), True),
        ("background_viewed", UUID(int=1), True),
        ("background_viewed", None, False),
        ("admin_login", UUID(int=1), False),
    ],
)
def test_only_task_subjects_open_the_task_page(event, subject, linked):
    """Task entries and views of one task's page link to it; other subjects don't."""
    assert task_subject({"event": event, "subject_id": subject}) is linked


@pytest.mark.parametrize(
    "roles",
    [
        frozenset({"administrator"}),
        frozenset({"staff"}),
        frozenset({"ministry_leader"}),
        frozenset({"staff", "ministry_leader"}),
    ],
)
def test_every_log_reader_can_open_the_linked_task_page(roles):
    """The "Open task" link is shown to every log reader, so each must be able
    to open the task page; a role split that breaks this must change the link.
    """
    principal = Principal(uuid4(), roles, frozenset({1}))
    for ministry_id in (None, 1):
        if allows(principal, Capability.SYSTEM_LOGS, ministry_id=ministry_id):
            assert allows(
                principal, Capability.BACKGROUND_WORK, ministry_id=ministry_id
            )
