"""The Mail message page's outcome panel and resolution question (#1563).

The model (``delivery_reads.email_outcome`` and ``question_choices``) is
checked against ``offered_actions`` for every combination the page can
meet, so an answer is allowed exactly when its action is offered: none the
server would refuse, none hidden that it would allow. The markup checks
render the browser fixtures' pages. The browser behaviour (disclosure, no
layout shift, in place) is in browser/test_delivery_outcome.py.
"""

import itertools
import re
from datetime import UTC, datetime

import pytest
from django.template.loader import render_to_string
from django.utils.html import escape

from parishkit.stewardship.jobs.delivery_metadata import NOTICE_ACTIONS
from parishkit.stewardship.jobs.delivery_reads import (
    QUESTIONS,
    SENDING_ACTIONS,
    UNAVAILABLE_REASONS,
    email_outcome,
    offered_actions,
    question_choices,
)

from .browser.delivery_components import components

NOW = datetime(2054, 10, 1, 12, tzinfo=UTC)
STATES = (
    "pending",
    "retry_wait",
    "submitting",
    "delivered",
    "delivery_unknown",
    "permanent_failure",
    "cancelled",
)


@pytest.mark.parametrize(
    ("state", "refused", "outcome"),
    [
        ("delivered", False, "delivered"),
        ("delivered", True, "needs_resolution"),
        ("delivery_unknown", False, "needs_resolution"),
        ("permanent_failure", True, "needs_resolution"),
        ("pending", False, "still_sending"),
        ("retry_wait", False, "still_sending"),
        ("submitting", False, "still_sending"),
        ("cancelled", False, "cancelled"),
    ],
)
def test_each_state_has_one_outcome_panel(state, refused, outcome):
    """A delivered email with a refused address still needs checking."""
    assert email_outcome(state, refused) == outcome


def blocked_reason(resolvable, campaign_state, can_resolve):
    """What read_detail passes as ``blocked`` for an email of a campaign.

    One read_detail cannot resolve (which the outbox_routing check makes
    impossible: only notices and Ministry leader emails have no campaign)
    keeps its default campaign state, archived (#2300 removed no_campaign).
    """
    if not resolvable or campaign_state == "archived":
        return "archived"
    return None if can_resolve else "held"


@pytest.mark.parametrize(
    ("state", "task", "campaign_state", "resolvable", "can_resolve", "can_retry"),
    list(
        itertools.product(
            STATES,
            (None, "running", "failed"),
            ("active", "closed", "archived"),
            (True, False),
            (True, False),
            (True, False),
        )
    ),
)
def test_answers_are_allowed_exactly_when_offered(
    state, task, campaign_state, resolvable, can_resolve, can_retry
):
    """Every allowed answer's action is offered, every offered decision is
    an allowed answer, and each refusal names the first reason that holds."""
    actions = (
        offered_actions(
            state,
            task,
            campaign_state=campaign_state,
            can_resolve=can_resolve,
            can_retry=can_retry,
        )
        if resolvable
        else []
    )
    blocked = blocked_reason(resolvable, campaign_state, can_resolve)
    rows = question_choices(
        state, task, actions, blocked=blocked, refusal="family_responded"
    )
    allowed = {row["action"] for row in rows if row["unavailable"] is None}
    assert allowed <= set(actions)
    # Every decision the page offers is an answer; only the bare note can
    # live outside the question (the closed "Add a note" form).
    assert set(actions) - {"note"} <= allowed
    for row in rows:
        reason = row["unavailable"]
        assert reason is None or reason in UNAVAILABLE_REASONS
        if reason is None:
            continue
        if blocked:
            assert reason == blocked
        elif task != "failed":
            assert reason == "task_running"
        else:
            # Only a send can be refused once the campaign accepts changes.
            assert row["action"] in SENDING_ACTIONS and not can_retry
            assert reason == "family_responded"
    expected = [action for _, action in QUESTIONS.get(state, ())]
    if state in {"pending", "retry_wait"} and task != "failed":
        expected = []
    if state == "cancelled":
        # A cancelled email asks nothing unless it is a report email still
        # to be counted (#1872), which the test below covers.
        expected = []
    assert [row["action"] for row in rows] == expected


@pytest.mark.parametrize("task", (None, "running", "failed"))
@pytest.mark.parametrize("can_resolve", (True, False))
def test_a_countable_cancelled_report_email_asks_whether_it_reached(task, can_resolve):
    """A report email once unknown, then cancelled, is offered both coverage
    decisions whatever its task did, since neither sends (#1872)."""
    actions = offered_actions(
        "cancelled",
        task,
        campaign_state="active",
        can_resolve=can_resolve,
        can_retry=True,
        countable=True,
    )
    rows = question_choices(
        "cancelled",
        task,
        actions,
        blocked=None if can_resolve else "held",
        refusal=None,
        countable=True,
    )
    assert [row["action"] for row in rows] == ["count_delivered", "count_unsent"]
    # Held changes are still asked about, but refused with the held reason.
    assert {row["unavailable"] for row in rows} == ({None} if can_resolve else {"held"})


@pytest.mark.parametrize("state", STATES)
def test_a_notice_asks_only_what_it_can_answer(state):
    """An operational or security notice (#2295) is never sent again: Not
    sure it arrived asks without the send-again question, every other state
    asks nothing, and no answer is ever shown unavailable once its task has
    failed, as an uncertain notice's always has."""
    actions = offered_actions(
        state, "failed", campaign_state=None, can_resolve=True, can_retry=False
    )
    rows = question_choices(
        state, "failed", actions, blocked=None, refusal=None, notice=True
    )
    if state == "delivery_unknown":
        assert [(row["choice"], row["action"]) for row in rows] == [
            ("sent", "accept"),
            ("not_sent", "confirm_unsent"),
            ("unsure", "note"),
        ]
    else:
        assert rows == []
    assert all(row["unavailable"] is None for row in rows)
    assert "resend" not in actions and set(actions) <= set(NOTICE_ACTIONS)


@pytest.mark.parametrize(
    ("task", "can_resolve", "reason"),
    [
        ("running", True, "notice_task_running"),
        ("failed", False, "notice_held"),
        ("running", False, "notice_held"),
    ],
)
def test_a_waiting_notice_names_its_own_reason(task, can_resolve, reason):
    """A notice whose answers can't be given yet (#2295) names a notice's
    reason, never a campaign's (held) or an email's (task_running)."""
    actions = offered_actions(
        "delivery_unknown",
        task,
        campaign_state=None,
        can_resolve=can_resolve,
        can_retry=False,
    )
    rows = question_choices(
        "delivery_unknown",
        task,
        actions,
        blocked=None if can_resolve else "notice_held",
        refusal=None,
        notice=True,
    )
    assert {row["unavailable"] for row in rows} - {None} == {reason}
    assert reason in UNAVAILABLE_REASONS


@pytest.mark.parametrize(
    ("path", "note"),
    [("/delivery-notice-task", True), ("/delivery-notice-held", False)],
)
def test_a_waiting_notice_shows_one_line_and_no_answer(path, note):
    """A waiting notice's panel says why in one line in place of the
    question: no radio, disabled or not, and no answer form. Its note, when
    offered, waits in the closed "Add a note" form."""
    html = fixture_page(path)
    outcome = panel(html)
    reason = UNAVAILABLE_REASONS["notice_task_running" if note else "notice_held"]
    assert f'<p class="help" id="notice-wait">{escape(reason)}' in outcome
    assert 'type="radio"' not in outcome
    assert "What did you find?" not in outcome and "<form" not in outcome
    assert ('<details id="delivery-note-disclosure"' in html) is note


def test_no_reason_names_a_campaign_less_email():
    """Every listed email is resolvable or settled from evidence (#2300),
    so no reason says an email belongs to no campaign."""
    assert "no_campaign" not in UNAVAILABLE_REASONS
    assert not any("no campaign" in str(text) for text in UNAVAILABLE_REASONS.values())


def test_a_leader_email_says_what_it_is_and_asks_only_what_applies():
    """A Ministry leader email that may not have arrived (#2300) explains
    itself, asks "What did you find?" without the send-again question, and
    shows no unavailable answer; It was not sent records it at once."""
    outcome = panel(fixture_page("/delivery-leader"))
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", outcome))
    assert '<p id="notice-about">This is a Ministry leader email' in outcome
    assert "Applying that review again sends a new one" in text
    assert "a halted mail sender can't be cleared on System health" in text
    assert "for this Ministry leader email to leader@example.org" in text
    assert re.findall(r'name="found" value="(\w+)"', outcome) == [
        "sent",
        "not_sent",
        "unsure",
    ]
    assert re.findall(r'name="action" value="(\w+)"', outcome) == [
        "accept",
        "confirm_unsent",
        "note",
    ]
    assert "disabled" not in outcome and 'name="again"' not in outcome
    assert "Send it again" not in outcome and "belong to no campaign" not in outcome
    assert "This is an operational notice" not in outcome


def test_a_failed_leader_email_says_how_a_new_one_is_sent():
    """A Failed Ministry leader email asks nothing and says that applying
    the review again sends a new one, not that it is retried (#2300)."""
    outcome = panel(fixture_page("/delivery-leader-failed"))
    assert "It is not sent again from here; to send a new one, apply" in outcome
    assert "A notice is never sent again." not in outcome
    assert "not tried again on its own" not in outcome
    assert 'type="radio"' not in outcome and "disabled" not in outcome


def test_a_failed_notice_says_it_is_never_sent_again():
    """A Failed notice asks nothing and never implies a retry by hand."""
    outcome = panel(fixture_page("/delivery-notice-failed"))
    assert "A notice is never sent again." in outcome
    assert "not tried again on its own" not in outcome
    assert 'type="radio"' not in outcome
    failed = panel(fixture_page("/delivery-failed"))
    assert "It is not tried again on its own." in failed
    assert "A notice is never sent again." not in failed


def test_a_refused_send_without_a_known_cause_has_the_generic_reason():
    """retry_refusal found nothing: the reason is the generic one."""
    (yes, _) = question_choices(
        "permanent_failure", "failed", ["note"], blocked=None, refusal=None
    )
    assert yes == {
        "choice": "send_again",
        "action": "retry_failed",
        "unavailable": "not_admitted",
    }


def fixture_page(path):
    """One browser fixture's Mail message page, rendered."""
    for name, template, context in components(NOW):
        if name == path:
            return render_to_string(f"stewardship/{template}.html", context)
    raise LookupError(path)


def panel(html):
    """The outcome panel's markup (the #delivery-resolve region)."""
    start = html.index('<div id="delivery-resolve"')
    return html[start : html.index('<div id="delivery-state"', start)]


@pytest.mark.parametrize(
    "path", ["/delivery-note-only", "/delivery-cancelled", "/delivery-queued"]
)
def test_settled_and_waiting_emails_draw_no_form_in_the_panel(path):
    """Delivered, cancelled and queued emails ask nothing: the note waits in
    its closed disclosure below (#1563)."""
    html = fixture_page(path)
    assert "<form" not in panel(html)
    assert '<details id="delivery-note-disclosure" data-keep-open>' in html


def test_a_settled_emails_history_is_closed():
    """Once an email is delivered its attempt history waits closed."""
    html = fixture_page("/delivery-note-only")
    assert '<details id="attempt-history-disclosure" data-keep-open>' in html
    assert '<details id="attempt-history-disclosure"' not in fixture_page("/delivery")


def test_the_question_radios_are_in_no_form():
    """Choosing an answer sends nothing: the radio buttons sit outside every
    form, and each answer's own form posts only its action's fields."""
    html = panel(fixture_page("/delivery"))
    forms = [match.span() for match in re.finditer(r"<form .*?</form>", html, re.S)]
    radios = [match.start() for match in re.finditer(r'<input type="radio"', html)]
    assert len(radios) == 5 and len(forms) == 4
    for radio in radios:
        assert not any(start < radio < end for start, end in forms)
    for form in re.findall(r"<form .*?</form>", html, re.S):
        names = set(re.findall(r'name="([^"]+)"', form))
        assert names <= {
            "csrfmiddlewaretoken",
            "command_id",
            "expected_version",
            "action",
            "note",
            "duplicate_acknowledged",
        }


def test_what_to_check_names_where_it_was_sent_and_when():
    """Not sure it arrived names the routed addresses, the type and the last
    attempt's time (browser-local)."""
    html = panel(fixture_page("/delivery-unknown-paused"))
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    assert "for this Initial invitation to head@example.org, spouse@example.org" in (
        text
    )
    assert re.search(r'sent around <time datetime="[^"]+" data-local-instant>', html)
    # The resend waits with the paused mail, which its answer says.
    assert "the new copy waits with the paused mail" in text


def test_a_failed_email_lists_its_refused_addresses():
    """Each refused address links its page, to be cleared after checking."""
    html = panel(fixture_page("/delivery-failed"))
    assert re.search(r'<a href="/admin/mail/refusals/[^"]+">head@example.org</a>', html)


def test_a_still_sending_email_says_when_it_tries_next():
    """Waiting to retry names the next attempt; paused links the controls."""
    waiting = panel(fixture_page("/delivery-waiting"))
    assert re.search(
        r'Next attempt: <time datetime="[^"]+" data-local-instant>', waiting
    )
    assert "<form" not in waiting
    stopped = panel(fixture_page("/delivery-sending"))
    assert "Try again now" in stopped and "<form" in stopped
    assert "Next attempt" not in stopped
    queued = panel(fixture_page("/delivery-queued"))
    assert 'href="/admin/mail/controls/"' in queued


class Answer:
    """A stand-in cursor that answers retry_refusal's one query."""

    def __init__(self, row):
        """Keep the row the query returns."""
        self.row = row
        self.queries = []

    def __enter__(self):
        """Use as the cursor itself."""
        return self

    def __exit__(self, *_):
        """Nothing to close."""

    def execute(self, sql, parameters):
        """Record the query."""
        self.queries.append(sql)

    def fetchone(self):
        """The row given."""
        return self.row


@pytest.mark.parametrize(
    ("purpose", "state", "row", "reason"),
    [
        # (open now, responded, reachable, paused)
        ("initial", "permanent_failure", (False, True, False, True), "campaign_ended"),
        (
            "reminder",
            "permanent_failure",
            (True, True, False, True),
            "family_responded",
        ),
        (
            "initial",
            "permanent_failure",
            (True, False, False, True),
            "family_unreachable",
        ),
        (
            "receipt",
            "permanent_failure",
            (False, True, False, False),
            "family_unreachable",
        ),
        ("initial", "permanent_failure", (True, False, True, True), "paused"),
        # A pause never fences the resend of an email that may have arrived.
        ("initial", "delivery_unknown", (True, False, True, True), None),
        ("receipt", "permanent_failure", (False, True, True, False), None),
        ("initial", "permanent_failure", None, None),
    ],
)
def test_a_refused_send_is_named_by_its_first_cause(
    monkeypatch, purpose, state, row, reason
):
    """retry_refusal names the first cause in the spec's order; only an
    invitation or reminder ends with the campaign or the Family's response."""
    from parishkit.stewardship.jobs import delivery_reads

    cursor = Answer(row)
    monkeypatch.setattr(delivery_reads.connection, "cursor", lambda: cursor)
    message = dict(
        mode="production",
        purpose=purpose,
        state=state,
        family_id="family",
        campaign_id="campaign",
    )
    assert delivery_reads.retry_refusal(message) == reason
    assert len(cursor.queries) == 1


def test_testing_mail_is_never_diagnosed(monkeypatch):
    """A Testing email's refused send keeps the generic reason, unread."""
    from parishkit.stewardship.jobs import delivery_reads

    cursor = Answer((False, True, False, True))
    monkeypatch.setattr(delivery_reads.connection, "cursor", lambda: cursor)
    message = dict(
        mode="testing",
        purpose="initial",
        state="permanent_failure",
        family_id="family",
        campaign_id="campaign",
    )
    assert delivery_reads.retry_refusal(message) is None
    assert cursor.queries == []
