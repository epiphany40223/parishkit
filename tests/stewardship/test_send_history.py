"""Family email send history (#432) without a database.

The counts come from the progress panel's statements (tested against
PostgreSQL); here the send identifier a link carries, the listing order and
reminder numbers, each count's Outgoing mail filter and the table the page
renders are checked.
"""

import re
from contextlib import contextmanager
from datetime import timedelta
from urllib.parse import parse_qs
from uuid import UUID

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.jobs import send_history
from parishkit.stewardship.jobs.send_history import ListedSend, SendKey, SendRow
from parishkit.stewardship.jobs.send_progress import progress
from parishkit.stewardship.web.tables import paginate

from .test_send_progress import NOW, counts

INVITATION = UUID(int=1)
REMINDER = UUID(int=2)
LATER = UUID(int=3)
# Schedule revisions: the first of each schedule, and a reminder moved later.
FIRST = UUID(int=11)
MOVED = UUID(int=12)


def test_a_send_key_round_trips_through_its_token():
    """The token a link carries reads back as the same send."""
    key = SendKey(INVITATION, FIRST, "production", 1)
    assert key.token == f"{INVITATION}:{FIRST}:production:1"
    assert SendKey.parse(key.token) == key
    assert SendKey.parse(f"{REMINDER}:{MOVED}:testing:0") == SendKey(
        REMINDER, MOVED, "testing", 0
    )


@pytest.mark.parametrize(
    "token",
    [
        "",
        f"{INVITATION}:{FIRST}",
        f"{INVITATION}:{FIRST}:production",
        f"{INVITATION}:production:1",
        f"{INVITATION}:{FIRST}:Production:1",
        f"{INVITATION}:{FIRST}:staging:1",
        f"{INVITATION}:{FIRST}:production:-1",
        f"{INVITATION}:{FIRST}:production:01",
        f"{INVITATION}:{FIRST}:production:1:2",
        f"ABCDEF00-0000-0000-0000-000000000001:{FIRST}:production:1",
        f" {INVITATION}:{FIRST}:production:1",
        f"{INVITATION}:{FIRST}:production:{'9' * 19}",
        None,
    ],
)
def test_a_malformed_send_is_refused(token):
    """Only the exact token form is accepted; anything else is a ValueError."""
    with pytest.raises(ValueError, match="Unknown Family email send"):
        SendKey.parse(token)


class FakeCursor:
    """A cursor returning the next fixed result for each statement executed."""

    def __init__(self, *results):
        self.results = list(results)
        self.executed = []

    def execute(self, statement, values):
        """Remember the statement and its values."""
        self.executed.append((statement, values))

    def fetchall(self):
        """The next fixed result."""
        return list(self.results.pop(0))


@contextmanager
def fake_connection(monkeypatch, *results):
    """Route ``send_history``'s statements to a FakeCursor returning ``results``."""
    cursor = FakeCursor(*results)

    class Connection:
        @contextmanager
        def cursor(self):
            yield cursor

    monkeypatch.setattr(send_history, "connection", Connection())
    yield cursor


def test_sends_list_newest_first_with_one_reminder_number_per_schedule(monkeypatch):
    """Reminders are numbered once, by their current due time, in every mode.

    REMINDER was due first and then moved after LATER, so LATER is now
    Reminder 1 and REMINDER Reminder 2, in Testing and in Production, for
    its old send as well as its new one. The old send is marked as changed.
    """
    day = timedelta(days=1)
    rows = [
        # definition, revision, mode, cycle, kind, scheduled, current
        # revision, newest email planned
        (INVITATION, FIRST, "testing", 0, "initial", NOW, True, NOW),
        (REMINDER, FIRST, "testing", 0, "reminder", NOW + day, False, NOW + day),
        (LATER, FIRST, "testing", 0, "reminder", NOW + 2 * day, True, NOW + 2 * day),
        (REMINDER, MOVED, "testing", 0, "reminder", NOW + 3 * day, True, NOW + 3 * day),
        (INVITATION, FIRST, "production", 1, "initial", NOW, True, NOW + 4 * day),
        (LATER, FIRST, "production", 1, "reminder", NOW + 2 * day, True, NOW + 5 * day),
    ]
    reminders = [(LATER,), (REMINDER,)]
    with fake_connection(monkeypatch, rows, reminders) as cursor:
        listed = send_history.list_sends(INVITATION)
    assert [values for _, values in cursor.executed] == [{"campaign": INVITATION}] * 2
    assert [
        (sent.key.definition, sent.key.revision, sent.key.mode, sent.number)
        for sent in listed
    ] == [
        (REMINDER, MOVED, "testing", 2),
        # Same scheduled time: the send planned later (Production) is newer.
        (LATER, FIRST, "production", 1),
        (LATER, FIRST, "testing", 1),
        (REMINDER, FIRST, "testing", 2),
        (INVITATION, FIRST, "production", None),
        (INVITATION, FIRST, "testing", None),
    ]
    assert [sent.replaced for sent in listed] == [
        False,
        False,
        False,
        True,
        False,
        False,
    ]
    assert [str(sent.name) for sent in listed] == [
        "Reminder 2",
        "Reminder 1",
        "Reminder 1",
        "Reminder 2",
        "Invitation",
        "Invitation",
    ]


def test_a_removed_reminder_has_no_number(monkeypatch):
    """A removed reminder has no current due time, so it is just "Reminder"."""
    rows = [(REMINDER, FIRST, "testing", 0, "reminder", NOW, False, NOW)]
    with fake_connection(monkeypatch, rows, []):
        (listed,) = send_history.list_sends(INVITATION)
    assert (listed.number, str(listed.name), listed.replaced) == (
        None,
        "Reminder",
        True,
    )


def row(*, emails=None, key=None, live=False, current=True, earlier=False, **values):
    """A counted send: a finished invitation unless ``values`` say otherwise."""
    base = dict(
        sent=1087,
        failed=4,
        prepare_failed=0,
        uncertain=0,
        not_needed=12,
        recent=0,
        started_at=NOW - timedelta(minutes=26),
        last_settled_at=NOW,
    )
    key = key or SendKey(INVITATION, FIRST, "production", 1)
    return SendRow(
        listed=ListedSend(key, "initial", NOW - timedelta(minutes=30), None, False),
        current=current,
        live=live,
        earlier=earlier,
        send=progress(counts(**(base | values))),
        emails={"delivered": 1087, "permanent_failure": 4, "cancelled": 12}
        if emails is None
        else emails,
    )


def test_each_outcome_links_to_exactly_its_send_and_state():
    """Sent, failed, uncertain and cancelled each carry the send and its state."""
    sent = row()
    outcomes = {outcome.state: outcome for outcome in sent.outcomes}
    assert list(outcomes) == [
        "delivered",
        "permanent_failure",
        "delivery_unknown",
        "cancelled",
    ]
    for state, outcome in outcomes.items():
        assert parse_qs(outcome.query) == {
            "send": [f"{INVITATION}:{FIRST}:production:1"],
            "state": [state],
        }
    assert [(o.count, o.listed) for o in outcomes.values()] == [
        (1087, 1087),
        (4, 4),
        (0, 0),
        (12, 12),
    ]
    assert sent.minutes == 26
    assert sent.send.total == 1091 and not sent.send.active


def test_a_send_not_started_has_no_duration():
    """Without a first email or a result there is nothing to time."""
    assert row(started_at=None, last_settled_at=None).minutes is None
    assert row(last_settled_at=None).minutes is None
    assert row(started_at=NOW).minutes == 0


def render(*rows, campaign=True):
    """Render the sends table for ``rows`` as the page would."""
    table = paginate(list(rows), {})
    return render_to_string(
        "stewardship/family-email-sends-table.html",
        {"campaign": object() if campaign else None, "table": table},
    )


def test_a_finished_send_links_each_count_to_its_emails():
    """Every non-zero linked count opens Outgoing mail filtered to it."""
    html = render(row())
    token = f"{INVITATION}%3A{FIRST}%3Aproduction%3A1"
    for state, count in (
        ("delivered", "1,087"),
        ("permanent_failure", "4"),
        ("cancelled", "12"),
    ):
        link = f'href="/admin/mail/outgoing/?send={token}&amp;state={state}"'
        assert re.search(re.escape(link) + f' aria-label="[^"]*">{count}</a>', html), (
            state
        )
    # Nothing uncertain: a plain zero, no link to an empty list.
    assert "state=delivery_unknown" not in html
    # Each link says what it lists to a screen reader.
    assert 'aria-label="1,087 sent emails of Invitation, Production: show them"' in html
    assert "26 minutes" in html
    assert '<td class="numeric nowrap">1,091</td>' in html
    assert "In progress" not in html
    assert "Invitation" in html and "Production" in html
    assert "before a return to Testing" not in html
    assert "before a return to Testing" in render(row(earlier=True))


def test_the_table_is_seven_readable_columns_with_the_dates_first():
    """#931: fewer, wider columns; details are labelled or muted lines."""
    html = render(row())
    headings = re.findall(r'<th scope="col"[^>]*>([^<]+)</th>', html)
    assert headings == [
        "When",
        "Email",
        "Total",
        "Sent",
        "Failed",
        "Not sure it arrived",
        "Not in the total",
    ]

    def pairs(cell):
        """The (label, value) lines of one dl.cell-pairs, tags stripped."""
        found = re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", cell, re.S)
        return [
            (label, re.sub(r"<[^>]+>", "", value).strip()) for label, value in found
        ]

    cells = html.split("<tbody>")[1]
    # When is the row's first cell: the scheduled time, both sending times
    # and the duration, each labelled on a line of its own.
    when = cells.split("<td>", 1)[1].split("</td>", 1)[0]
    assert when.lstrip().startswith('<dl class="cell-pairs">')
    assert [label for label, _ in pairs(when)] == [
        "Scheduled",
        "First email",
        "Latest result",
        "Duration",
    ]
    assert pairs(when)[-1] == ("Duration", "26 minutes")
    # The mode is a muted line under the email's name.
    assert re.search(
        r'<span class="cell-line">Invitation</span>\s*'
        r'<span class="cell-detail">Production</span>',
        html,
    )
    # The counts outside the total are one labelled list; Cancelled links.
    counts = html.split('<dl class="cell-pairs cell-counts">')[1].split("</dl>")[0]
    assert "state=cancelled" in counts
    assert pairs(counts) == [
        ("Cancelled", "12"),
        ("Not needed", "12"),
        ("Couldn't be emailed", "0"),
        ("Skipped: Reminder WorkGroup", "0"),
        ("Held", "0"),
    ]
    # A send not started says so, with no result or duration lines.
    waiting = render(row(started_at=None, last_settled_at=None))
    assert "<dd>Not started</dd>" in waiting and "Latest result" not in waiting


def test_failures_before_preparation_show_the_count_and_link_only_the_emails():
    """5 failed, 2 before an email existed: the link lists the 3 there are."""
    html = render(row(failed=5, prepare_failed=2, emails={"permanent_failure": 3}))
    assert '>5<span class="cell-line"><a ' in html
    assert "show 3" in html


def test_only_the_send_the_panel_shows_links_to_it():
    """Two sends in progress: only the panel's links; both say In progress."""
    invitation = row(remaining=455, last_settled_at=None)
    reminder = row(key=SendKey(REMINDER, FIRST, "production", 1), remaining=7, sent=3)
    rows = send_history.mark_live([invitation, reminder], reminder.send.counts)
    assert [r.live for r in rows] == [False, True]
    html = render(*rows)
    assert html.count('href="/admin/mail/family-progress/"') == 1
    assert html.count("In progress") == 2
    assert "455 to send" in html and "7 to send" in html


def test_no_row_links_when_the_panel_shows_another_send():
    """A panel send not on this page, no send, or an ambiguous match: no link."""
    running = row(remaining=455, last_settled_at=None)
    elsewhere = row(remaining=1, sent=1, last_settled_at=None)
    assert not send_history.mark_live([running], elsewhere.send.counts)[0].live
    assert not send_history.mark_live([running], None)[0].live
    twin = row(
        key=SendKey(REMINDER, FIRST, "production", 1),
        remaining=455,
        last_settled_at=None,
    )
    assert not any(
        r.live for r in send_history.mark_live([running, twin], running.send.counts)
    )
    # Another mode's leftovers are never the panel's send.
    other = row(key=SendKey(REMINDER, MOVED, "testing", 0), current=False, remaining=3)
    assert not send_history.mark_live([other], other.send.counts)[0].live


def test_an_unknown_total_says_so():
    """While the Family list is refreshed, the total is not known yet."""
    html = render(row(live=True, remaining=2, unplanned=None))
    assert "total not known yet" in html and "Not known yet" in html
    assert "None yet" not in html


def test_no_sends_yet_says_so():
    """An empty history explains itself instead of showing an empty table."""
    assert "No invitation or reminder has been sent in this campaign yet." in render()


def test_the_idle_progress_panel_links_to_past_sends():
    """With no send in progress, the panel offers the send history."""
    from .test_send_progress import render as panel

    finished = progress(counts(sent=3, last_settled_at=NOW))
    assert 'href="/admin/mail/family-history/">See past sends' in panel(finished)
    assert 'href="/admin/mail/family-history/">See past sends' in panel(None)
