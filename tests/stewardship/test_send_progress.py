"""Family send progress arithmetic and the Mail queue's live region, without a database.

The counts come from ``read_send`` and ``read_queue`` (tested against
PostgreSQL); here the rate, finish estimate and percentage are checked for
empty, starting, running, stalled, paused and finished sends, and the Mail
queue's status template (#1562, with the Family send panel of #413) for
what it shows, when it keeps polling and what screen readers hear.
"""

import math
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.jobs.delivery_metadata import LISTED_PURPOSES, other_mode
from parishkit.stewardship.jobs.mail_queue import (
    QUEUE_PURPOSES,
    KindCounts,
    QueueCounts,
    _column,
)
from parishkit.stewardship.jobs.mail_queue_views import (
    GIVE_UP_MILLISECONDS,
    POLL_MILLISECONDS,
    _announcement,
    _tiles,
)
from parishkit.stewardship.jobs.send_progress import (
    RATE_WINDOW,
    SendCounts,
    progress,
)

NOW = datetime(2026, 10, 3, 14, tzinfo=UTC)


def counts(**values):
    """A send observed at NOW; every count defaults to zero."""
    base = dict(
        kind="initial",
        sent=0,
        failed=0,
        uncertain=0,
        prepare_failed=0,
        remaining=0,
        waiting=0,
        unreachable=0,
        not_needed=0,
        started_at=NOW - timedelta(minutes=10),
        last_settled_at=None,
        recent=0,
        since=NOW - RATE_WINDOW,
        now=NOW,
    )
    return SendCounts(**(base | values))


def test_a_running_send_rates_the_recent_window_and_estimates_its_finish():
    """500 settled in the last 5 minutes is 100 a minute; 600 left is 6 minutes."""
    sent = progress(
        counts(sent=480, failed=10, uncertain=10, remaining=600, recent=500)
    )
    assert (sent.total, sent.done, sent.percent, sent.active) == (1100, 500, 45, True)
    assert sent.rate == Decimal("100.0")
    assert sent.minutes_left == 6
    assert sent.finish_at == NOW + timedelta(minutes=6)
    assert sent.finished_at is None


def test_a_young_send_is_measured_from_its_own_start():
    """Two minutes in, 60 settled is 30 a minute, not 12 over five minutes."""
    sent = progress(
        counts(
            sent=60,
            remaining=1040,
            recent=60,
            started_at=NOW - timedelta(minutes=2),
        )
    )
    assert sent.rate == Decimal("30.0")
    assert sent.minutes_left == 35  # 1040 / 30 = 34.7, rounded up


@pytest.mark.parametrize(
    ("started", "recent"),
    [
        (NOW - timedelta(seconds=10), 5),  # too short a sample
        (None, 0),  # nothing prepared yet
    ],
)
def test_no_rate_or_estimate_without_enough_to_measure(started, recent):
    """A rate over a few seconds, or before anything started, would mislead."""
    sent = progress(counts(remaining=1100, recent=recent, started_at=started))
    assert sent.rate is None and sent.finish_at is None and sent.minutes_left is None
    assert sent.percent == 0 and sent.active


def test_a_stalled_send_shows_a_zero_rate_and_no_estimate():
    """Nothing settled for five minutes: rate 0, and no division by zero."""
    sent = progress(counts(sent=100, remaining=1000, recent=0))
    assert sent.rate == Decimal("0.0") and sent.stalled
    assert sent.finish_at is None


def test_a_paused_send_keeps_its_rate_but_gives_no_finish_estimate():
    """Paused emails wait for resume, so no rate can predict when they finish.

    The pause control decides, not held messages: workers hold each message
    only when they reach it, so just after a pause none may be held yet.
    """
    sent = progress(counts(sent=100, remaining=1000, recent=100), paused=True)
    assert sent.paused and sent.rate == Decimal("20.0")
    assert sent.finish_at is None and sent.minutes_left is None
    # A finished send is not "paused": nothing is waiting for the resume.
    assert not progress(counts(sent=100), paused=True).paused


def test_held_reminders_and_unemailed_families_stay_out_of_the_total():
    """A reminder held behind a failed invitation must not keep the send open."""
    sent = progress(
        counts(
            kind="reminder",
            sent=90,
            waiting=6,
            unreachable=3,
            not_needed=40,
            started_at=NOW - timedelta(minutes=10),
            last_settled_at=NOW - timedelta(minutes=2),
        )
    )
    assert (sent.total, sent.percent, sent.active) == (90, 100, False)
    assert sent.finished_at == NOW - timedelta(minutes=2)


def test_a_finished_send_reports_its_end_and_average_rate():
    """1,100 settled over 22 minutes is 50 a minute; nothing is estimated."""
    sent = progress(
        counts(
            sent=1095,
            failed=2,
            uncertain=3,
            not_needed=7,
            started_at=NOW - timedelta(minutes=30),
            last_settled_at=NOW - timedelta(minutes=8),
        )
    )
    assert (sent.total, sent.percent, sent.active) == (1100, 100, False)
    assert sent.rate == Decimal("50.0")
    assert sent.finished_at == NOW - timedelta(minutes=8)
    assert sent.finish_at is None


def test_an_empty_send_is_finished_without_a_rate():
    """Every Family already responded: nothing to send, nothing to divide."""
    sent = progress(counts(not_needed=40))
    assert (sent.total, sent.percent, sent.active) == (0, 100, False)
    assert sent.rate is None and sent.finished_at is None


def test_the_percentage_reaches_100_only_when_nothing_remains():
    """1,099 of 1,100 is 99%, so the bar never claims done too early."""
    assert progress(counts(sent=1099, remaining=1)).percent == 99


def test_announcements_change_only_by_quarter_and_at_the_end():
    """Screen readers hear a few milestones, not every poll's counts."""
    words = [
        _announcement(progress(counts(sent=done, remaining=100 - done)))
        for done in (0, 10, 24, 25, 49, 50, 99)
    ]
    assert len(set(words)) == 4
    assert words[0] == words[1] == words[2]
    assert "25%" in words[3] and "50%" in words[5] and "75%" in words[6]
    assert (
        _announcement(progress(counts(sent=100)))
        == "No Family email send is in progress."
    )
    paused = progress(counts(sent=10, remaining=90), paused=True)
    assert _announcement(paused) == "Family email send paused."
    assert _announcement(None) == ""


def queue(mode="production", other=0, **kinds):
    """A Mail queue: ``kinds`` maps a purpose to its four counts."""
    return QueueCounts(
        mode=mode,
        kinds=tuple(
            KindCounts(purpose, *kinds.get(purpose, ())) for purpose in QUEUE_PURPOSES
        ),
        other_mode_queued=other,
    )


def render(send, *, upcoming=False, queued=None, **extra):
    """Render the Mail queue's status region as the page or the fragment would.

    As the view does, the region keeps polling whenever the system is
    available, campaign or not.
    """
    queued = queued or queue()
    paused = bool(send and send.paused)
    return render_to_string(
        "stewardship/mail-queue-status.html",
        {
            "campaign": object(),
            "testing": False,
            "paused": paused,
            "queue": queued,
            "tiles": _tiles(queued, paused),
            "other_mode": other_mode(queued.mode),
            "send": send,
            "upcoming": upcoming,
            "follow": True,
            "announcement": _announcement(send),
            "poll_interval": POLL_MILLISECONDS,
            "give_up": GIVE_UP_MILLISECONDS,
        }
        | extra,
    )


def region(html):
    """The opening tag of the Mail queue's updated region."""
    return re.search(r'<section[^>]*data-live-status="mail-queue"[^>]*>', html)[0]


def test_a_running_send_polls_with_a_labelled_progress_bar():
    """The region keeps polling at a fixed pace; its bar has a visible label."""
    html = render(
        progress(counts(sent=480, failed=10, uncertain=10, remaining=600, recent=500))
    )
    tag = region(html)
    assert "data-live-pending" in tag
    assert f'data-live-interval="{POLL_MILLISECONDS}"' in tag
    # Three hours without new counts, renewed by each change, before the
    # pace slows; the page has no Refresh link to name (#1586).
    assert 'data-live-give-up="10800000"' in tag
    assert "data-live-refresh-label" not in tag
    assert 'data-live-announce="Family email send in progress, 25% done."' in tag
    # The counts change every poll, so the region itself is not aria-live.
    assert "aria-live" not in tag and 'role="status"' not in tag
    assert (
        '<progress value="500" max="1100" aria-labelledby="send-progress-label">'
        in (html)
    )
    assert 'id="send-progress-label">500 of 1,100 emails finished (45%)' in html
    for text in ("Sent", "Remaining", "Failed", "Not sure it arrived"):
        assert f"<dt>{text}</dt>" in html
    assert "100.0 emails per minute" in html
    assert "about 6 minutes" in html
    # Each kind links to its own filtered list.
    # In the mode the counts read, so Sent mail lists those emails (#1576).
    assert (
        'href="/admin/mail/sent/?state=delivery_unknown&amp;mode=production">'
        "Review emails that may" in html
    )
    assert (
        'href="/admin/mail/sent/?state=permanent_failure&amp;mode=production">'
        "Review failed" in html
    )
    assert "failed before they were prepared" not in html
    assert "<script" not in html


def test_a_finished_send_is_one_line_with_no_bar_and_the_page_keeps_checking():
    """Moment in time: no send in progress, a summary line, Sent mail."""
    html = render(
        progress(
            counts(
                sent=1090,
                failed=4,
                uncertain=1,
                unreachable=3,
                not_needed=7,
                started_at=NOW - timedelta(minutes=30),
                last_settled_at=NOW - timedelta(minutes=8),
            )
        )
    )
    assert "No Family email send is in progress right now" in html
    assert "Last send: Invitation email, finished <time" in html
    assert "1,090 sent, 4 failed, 1 not sure it arrived, 10 not sent." in html
    assert 'href="/admin/mail/sent/">Sent mail</a>' in html
    assert "<progress" not in html and "emails finished" not in html
    # Still checking, so the next send appears by itself.
    assert "data-live-pending" in region(html)


def test_a_paused_send_says_so_instead_of_estimating():
    """A pause stops the send; the page says so and estimates nothing."""
    html = render(progress(counts(sent=100, remaining=1000, recent=100), paused=True))
    assert "<strong>Paused.</strong>" in html
    assert "Not while delivery is paused" in html
    assert "Sending Family emails" not in html
    assert "data-live-pending" in region(html)


def test_a_stalled_send_says_nothing_finished_recently():
    """Not "0.0 emails per minute" and an unexplained missing estimate."""
    html = render(progress(counts(sent=100, remaining=1000, recent=0)))
    assert "No emails finished in the last 5 minutes" in html
    assert "Not while no emails are finishing" in html
    assert "emails per minute" not in html


def test_a_cancelled_send_is_not_in_progress():
    """Every remaining email cancelled (the schedule moved): over, no bar."""
    sent = progress(counts(sent=65, not_needed=110, last_settled_at=NOW))
    assert (sent.total, sent.active) == (65, False)
    html = render(sent)
    assert "No Family email send is in progress right now" in html
    assert "65 sent, 0 failed, 0 not sure it arrived, 110 not sent." in html
    assert "<progress" not in html


def test_reminder_buckets_and_preparation_failures_are_named():
    """Held reminders, unreachable Families and early failures each show."""
    html = render(
        progress(
            counts(
                kind="reminder",
                sent=50,
                failed=2,
                prepare_failed=2,
                waiting=3,
                unreachable=4,
                not_needed=5,
                remaining=1,
                last_settled_at=NOW,
            )
        )
    )
    assert "Reminder email" in html
    assert "<dt>Held: invitation failed or not sure it arrived</dt><dd>3</dd>" in html
    assert "<dd>4</dd>" in html and "<dd>5</dd>" in html
    assert "Not needed (already responded or replaced)" in html
    # Held reminders point at the invitations that hold them.
    assert "state=delivery_unknown" in html and "state=permanent_failure" in html
    assert "task_type=family_mail_prepare" in html


@pytest.mark.parametrize(
    ("campaign", "upcoming", "text"),
    [
        (
            None,
            False,
            "There is no current campaign, so no Family email is being sent.",
        ),
        (object(), False, "No Family email send is in progress right now"),
        (object(), True, "Waiting for the next emails to be scheduled"),
    ],
)
def test_before_the_first_send_the_page_keeps_checking(campaign, upcoming, text):
    """A page opened before the first email switches to it by itself.

    With no campaign it still checks: notices and Ministry leader emails
    are queued without one (#1562).
    """
    html = render(None, campaign=campaign, upcoming=upcoming)
    assert text in html
    assert "data-live-pending" in region(html)
    assert "<progress" not in html and "Last send" not in html


def test_a_finished_send_keeps_checking_when_the_next_one_is_due():
    """The invitation is done and a reminder is due within the hour."""
    html = render(progress(counts(sent=10, last_settled_at=NOW)), upcoming=True)
    assert "No Family email send is in progress right now" in html
    assert "Waiting for the next emails to be scheduled" in html
    assert "Last send: Invitation email" in html
    assert "data-live-pending" in region(html)


def test_a_held_send_says_held_instead_of_sending():
    """Planning held: the owed emails still count, but nothing claims to send."""
    sent = progress(counts(sent=10, remaining=90, unplanned=90, held=True))
    assert sent.active
    html = render(sent)
    assert "<strong>Held.</strong>" in html
    assert "Sending Family emails" not in html
    assert _announcement(sent) == "Family email send held."


def test_families_not_yet_planned_count_in_the_total_from_the_start():
    """12 sent of 1,103 Families is 1%, not 12 of the 24 planned so far (50%).

    ``read_send`` adds the Families planning has not reached to remaining
    (and to not prepared); the percentage and estimate use the whole send.
    """
    sent = progress(
        counts(sent=12, remaining=1091, unprepared=1079, unplanned=1079, recent=12)
    )
    assert (sent.total, sent.done, sent.percent, sent.active) == (1103, 12, 1, True)
    assert sent.minutes_left == math.ceil(1091 / sent.rate)
    html = render(sent)
    assert "12 of 1,103 emails finished (1%)" in html
    assert '<progress value="12" max="1103"' in html
    assert "<dd>1,091 (of these, 1,079 not prepared yet)</dd>" in html


def test_an_unknown_total_shows_counts_without_a_percentage():
    """While the Family list is refreshed, no share of a partial total is shown."""
    sent = progress(
        counts(sent=12, remaining=12, unprepared=0, unplanned=None, recent=12)
    )
    assert (sent.total, sent.percent, sent.active) == (None, None, True)
    # A rate can still be measured, but there is nothing to estimate from.
    assert sent.rate is not None and sent.finish_at is sent.minutes_left is None
    assert _announcement(sent) == (
        "Family email send in progress; the total is not known yet."
    )
    html = render(sent)
    assert "12 emails finished so far." in html
    assert "The full total is not known yet" in html
    assert "%" not in html.split('id="send-progress-label">')[1].split("</p>")[0]
    # An indeterminate bar: no value, so no share is claimed.
    assert '<progress aria-labelledby="send-progress-label">' in html
    assert "<dt>Total</dt><dd>Not known yet</dd>" in html
    assert "<dd>12 so far</dd>" in html
    assert "Not until the full total is known" in html
    assert "data-live-pending" in region(html)
    # Even with nothing planned left, the send is not finished while unknown.
    assert progress(counts(sent=12, unplanned=None)).active


def test_reminder_workgroup_skips_are_not_sent_and_not_remaining():
    """Reminders the Reminder WorkGroup skipped (#861) count as not sent."""
    observed = counts(kind="reminder", sent=3, workgroup=2, not_needed=1)
    assert observed.not_sent == 3 and not observed.in_progress
    assert progress(observed).total == 3


def test_queue_columns_split_held_and_sending_messages():
    """A held message is Paused; one being handed over is Sending now."""
    assert _column("pending", False) == "queued"
    assert _column("retry_wait", False) == "retry_wait"
    assert _column("pending", True) == _column("retry_wait", True) == "paused"
    assert _column("submitting", False) == _column("submitting", True) == "sending"
    assert other_mode("production") == "testing"
    assert other_mode("testing") == "production"


def test_queue_tiles_sum_every_kind_and_say_whether_delivery_is_paused():
    """Four tiles, each a column summed over every kind (#1562)."""
    queued = queue(
        initial=(5, 2, 1, 0), operational=(1, 0, 0, 0), reminder=(0, 0, 0, 3)
    )
    assert queued.total == 12
    tiles = {item.key: item for item in _tiles(queued, True)}
    assert [
        tiles[key].text for key in ("queued", "sending", "retry-wait", "paused")
    ] == [
        "6",
        "2",
        "1",
        "3",
    ]
    assert tiles["paused"].details == ("Live delivery is paused",)
    running = {item.key: item for item in _tiles(queued, False)}
    assert running["paused"].details == ("Live delivery is not paused",)
    # Only Production mail can be paused.
    testing = {item.key: item for item in _tiles(queue("testing"), False)}
    assert testing["paused"].details == ("Testing email is never paused",)


def test_queue_table_draws_every_kind_and_links_only_kinds_with_mail():
    """Every kind always has a row; a Kind links Sent mail only while queued."""
    html = render(None, queued=queue(initial=(4, 1, 0, 0)))
    rows = re.findall(r'<tr data-kind="([a-z_]+)">', html)
    # Every kind Sent mail lists, in its order, the Ministry leader emails
    # last (#1562).
    assert tuple(rows) == QUEUE_PURPOSES == LISTED_PURPOSES
    assert rows[-1] == "leader_email"
    assert (
        '<a href="/admin/mail/sent/?purpose=initial&amp;state=still_sending'
        '&amp;mode=production">Initial invitation</a>' in html
    )
    assert '<th scope="row">Reminder</th>' in html
    assert '<td class="numeric mail-queue-zero">0</td>' in html
    assert '<td class="numeric">4</td>' in html
    # Nothing in the other mode: its line keeps its place, empty.
    assert '<p class="mail-queue-other"></p>' in html


def test_queue_other_mode_line_counts_and_links_that_mode():
    """The other mode's queued emails are named, never added to the tiles."""
    html = render(None, queued=queue(other=3))
    assert (
        '<a href="/admin/mail/sent/?state=still_sending&amp;mode=testing">'
        "3 emails queued in Testing</a>" in html
    )
    single = render(None, queued=queue("testing", other=1))
    assert "1 email queued in Production</a>" in single


def test_queued_ministry_leader_emails_link_sent_mail():
    """Sent mail lists Ministry leader emails (#1562), so their Kind links
    it while one is queued, as every other kind's does."""
    html = render(None, queued=queue(leader_email=(1, 0, 0, 0)))
    row = html.split('<tr data-kind="leader_email">')[1].split("</tr>")[0]
    assert (
        '<a href="/admin/mail/sent/?purpose=leader_email&amp;state=still_sending'
        '&amp;mode=production">Ministry leader email</a>' in row
    )
    assert '<td class="numeric">1</td>' in row
