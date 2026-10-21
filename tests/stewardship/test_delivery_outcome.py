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
    """What read_detail passes as ``blocked``."""
    if not resolvable:
        return "no_campaign"
    if campaign_state == "archived":
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
    assert [row["action"] for row in rows] == expected


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
