"""Outgoing mail's bulk actions, without a database (#382 M4).

The selection rules shared with each message's page, the preview's
validation and binding, and how one application treats each message's
outcome: resolved, skipped when its own guard refuses, stopped when the
Administrator loses access or the database fails, and batched.
"""

import time
from contextlib import nullcontext
from uuid import UUID, uuid4, uuid5

import pytest
from django.core import signing
from django.db import DatabaseError, IntegrityError

from parishkit.stewardship.jobs import delivery_bulk
from parishkit.stewardship.jobs.delivery_reads import offered_actions
from parishkit.stewardship.storage import StaleRecordError

ACTOR = uuid4()


@pytest.mark.parametrize(
    "state,task,campaign,resolve,retry,expected",
    [
        ("delivery_unknown", "failed", "active", True, False, True),
        ("delivery_unknown", "running", "active", True, True, False),
        ("delivery_unknown", "failed", "archived", True, True, False),
        ("delivery_unknown", "failed", "active", False, True, False),
        ("permanent_failure", "failed", "active", True, True, True),
        ("permanent_failure", "failed", "active", True, False, False),
        ("permanent_failure", None, "active", True, True, False),
    ],
)
def test_bulk_actions_follow_each_message_page(
    state, task, campaign, resolve, retry, expected
):
    """A bulk action covers a message only when its own page offers it."""
    kind = "confirm_unsent" if state == "delivery_unknown" else "retry_failed"
    offered = offered_actions(
        state, task, campaign_state=campaign, can_resolve=resolve, can_retry=retry
    )
    assert (kind in offered) is expected


@pytest.mark.parametrize(
    "kind,purpose,note,checked",
    [
        ("resend", "all", "Evidence", False),
        ("accept", "all", "Evidence", False),
        ("retry_failed", "family_test", "Evidence", False),
        ("retry_failed", "unknown", "Evidence", False),
        ("retry_failed", "all", "   ", False),
        ("retry_failed", "all", "x" * 2001, False),
        ("retry_failed", "all", "Evidence", True),
        ("confirm_unsent", "all", "Evidence", False),
        ("confirm_unsent", "all", "", True),
        ("confirm_unsent", "all", "Evidence", "yes"),
    ],
)
def test_a_preview_needs_a_valid_choice_note_and_check(kind, purpose, note, checked):
    """Refused before any database read: resend is never offered in bulk."""
    with pytest.raises(ValueError):
        delivery_bulk.preview(
            ACTOR, kind=kind, purpose=purpose, note=note, checked=checked
        )


def token(**binding):
    """A signed preview as ``preview`` makes it."""
    values = dict(
        actor=str(ACTOR),
        command=str(uuid4()),
        kind="confirm_unsent",
        purpose="all",
        note_digest=delivery_bulk.note_digest("Evidence"),
        items=[[str(uuid4()), 3]],
        skipped=[],
        issued=int(time.time()),
    )
    return signing.dumps(values | binding, salt=delivery_bulk.SALT, compress=True)


def test_a_preview_binds_its_administrator_and_shape():
    """Another Administrator, an altered or malformed preview is refused."""
    loaded = delivery_bulk.load_preview(token(), ACTOR, "Evidence")
    assert loaded["kind"] == "confirm_unsent" and loaded["note"] == "Evidence"
    with pytest.raises(PermissionError):
        delivery_bulk.load_preview(token(), uuid4(), "Evidence")
    with pytest.raises(signing.BadSignature):
        delivery_bulk.load_preview(token()[:-3] + "abc", ACTOR, "Evidence")
    for binding in (dict(kind="resend"), dict(items=[]), dict(note="Evidence")):
        with pytest.raises(ValueError):
            delivery_bulk.load_preview(token(**binding), ACTOR, "Evidence")
    with pytest.raises(ValueError):
        delivery_bulk.load_preview("", ACTOR, "Evidence")
    with pytest.raises(ValueError):
        delivery_bulk.load_preview(token(), ACTOR, "  ")


def test_the_token_carries_only_a_digest_of_the_note():
    """The note is posted again and must match; it is not readable in the token."""
    signed = token()
    assert "Evidence" not in str(
        signing.loads(signed, salt=delivery_bulk.SALT, max_age=60)
    )
    with pytest.raises(signing.BadSignature):
        delivery_bulk.load_preview(signed, ACTOR, "Other evidence")
    # A browser may post the note's line breaks either way.
    multi = token(note_digest=delivery_bulk.note_digest("Line one\nLine two"))
    assert delivery_bulk.load_preview(multi, ACTOR, "Line one\r\nLine two")


def test_a_continued_preview_expires_with_the_original():
    """Re-signing for Continue never extends the 15 minutes."""
    old = token(issued=int(time.time()) - delivery_bulk.PREVIEW_SECONDS - 1)
    with pytest.raises(signing.SignatureExpired):
        delivery_bulk.load_preview(old, ACTOR, "Evidence")


class Outcomes:
    """A stand-in for ``resolve_delivery`` and the resolution table.

    ``outcomes`` maps a message index to what resolving it does: None
    resolves it, an exception is raised. ``done`` holds command ids whose
    resolution already exists.
    """

    def __init__(self, monkeypatch, items, outcomes, done=()):
        self.items, self.outcomes, self.calls = items, outcomes, []
        self.done = set(done)
        self.audits = []
        bulk = self

        class Resolutions:
            """Answers ``filter(pk=...).exists()`` from ``done``."""

            class objects:
                @staticmethod
                def filter(pk):
                    return type("Q", (), {"exists": lambda self: pk in bulk.done})()

        monkeypatch.setattr(delivery_bulk, "DeliveryResolution", Resolutions)
        monkeypatch.setattr(delivery_bulk, "resolve_delivery", self.resolve)
        monkeypatch.setattr(
            delivery_bulk,
            "record_action",
            lambda action, **values: self.audits.append(values["context"]),
        )
        monkeypatch.setattr(
            delivery_bulk.transaction, "atomic", lambda *args, **kwargs: nullcontext()
        )

    def resolve(self, store, actor, *, message_id, command_id, **values):
        """Resolve or raise as ``outcomes`` says for this message."""
        index = [UUID(item[0]) for item in self.items].index(message_id)
        self.calls.append(index)
        outcome = self.outcomes.get(index)
        if outcome is not None:
            raise outcome
        self.done.add(command_id)


def binding(count):
    """A confirm-unsent preview of ``count`` messages."""
    return dict(
        actor=str(ACTOR),
        command=str(uuid4()),
        kind="confirm_unsent",
        purpose="all",
        note_digest=delivery_bulk.note_digest("Evidence"),
        items=[[str(uuid4()), 2] for _ in range(count)],
        skipped=[],
        issued=int(time.time()),
        note="Evidence",
    )


def apply(preview, *, admit=lambda: None, limit=delivery_bulk.BATCH):
    """Apply ``preview`` as the page does, outside any real scope."""
    return delivery_bulk.apply_preview(
        None,
        ACTOR,
        preview,
        scope=nullcontext,
        admit=admit,
        preparation_inputs=None,
        limit=limit,
    )


def refused_check():
    """The message's own SQL guard refusing (a check violation)."""
    cause = Exception("check violation")
    cause.sqlstate = "23514"
    error = IntegrityError("refused")
    error.__cause__ = cause
    return error


def test_each_message_is_resolved_alone_and_refusals_are_skipped(monkeypatch):
    """A changed or refused message is counted, never forced."""
    preview = binding(5)
    fake = Outcomes(
        monkeypatch,
        preview["items"],
        {
            1: StaleRecordError("changed"),
            2: PermissionError("scope"),
            3: refused_check(),
        },
    )
    result = apply(preview)
    assert fake.calls == [0, 1, 2, 3, 4]
    assert (result.resolved, result.newly, result.skipped, result.remaining) == (
        2,
        2,
        3,
        0,
    )
    assert fake.audits == [{"outcome": "changed", "count": 2, "matching_count": 5}]
    # Applying it again resolves nothing twice and retries only the skipped.
    fake.outcomes = {}
    again = apply(preview)
    assert fake.calls[5:] == [1, 2, 3]
    assert (again.resolved, again.newly, again.skipped) == (5, 3, 0)


def test_the_command_id_is_derived_from_the_preview_and_message(monkeypatch):
    """So a repeated confirmation replays instead of repeating."""
    preview = binding(1)
    fake = Outcomes(monkeypatch, preview["items"], {})
    apply(preview)
    assert fake.done == {uuid5(UUID(preview["command"]), preview["items"][0][0])}


def test_a_batch_leaves_the_rest_for_continue(monkeypatch):
    """At most ``limit`` messages are attempted per application."""
    preview = binding(5)
    fake = Outcomes(monkeypatch, preview["items"], {})
    first = apply(preview, limit=2)
    assert (first.resolved, first.remaining) == (2, 3)
    second = apply(preview, limit=2)
    assert (second.resolved, second.newly, second.remaining) == (4, 2, 1)
    third = apply(preview, limit=2)
    assert (third.resolved, third.remaining) == (5, 0)
    assert third.token is None
    assert fake.calls == [0, 1, 2, 3, 4]


def continued(result):
    """The binding Continue posts back: ``result``'s token, loaded."""
    return delivery_bulk.load_preview(result.token, ACTOR, "Evidence")


def test_continue_moves_past_what_a_batch_skipped(monkeypatch):
    """A full batch of skipped emails never stalls the rest of the preview."""
    preview = binding(5)
    fake = Outcomes(
        monkeypatch,
        preview["items"],
        {0: StaleRecordError("changed"), 1: StaleRecordError("changed")},
    )
    first = apply(preview, limit=2)
    assert (first.resolved, first.skipped, first.remaining) == (0, 2, 3)
    second = apply(continued(first), limit=2)
    # The skipped are counted again, not retried, and the batch moves on.
    assert fake.calls == [0, 1, 2, 3]
    assert (second.resolved, second.skipped, second.remaining) == (2, 2, 1)
    third = apply(continued(second), limit=2)
    assert fake.calls == [0, 1, 2, 3, 4]
    assert (third.resolved, third.newly, third.skipped, third.remaining) == (
        3,
        1,
        2,
        0,
    )
    assert third.token is None
    # Continue's preview keeps the original command (so replays still
    # replay) and issue time.
    assert continued(first)["command"] == preview["command"]
    assert continued(first)["issued"] == preview["issued"]


def test_lost_access_or_an_outage_stops_the_run_but_is_audited(monkeypatch):
    """Only the message's own refusal is skipped; anything else stops."""
    preview = binding(3)
    fake = Outcomes(monkeypatch, preview["items"], {1: PermissionError("signed out")})

    def admit():
        """The Administrator's sign-in ended."""
        raise PermissionError("signed out")

    with pytest.raises(PermissionError):
        apply(preview, admit=admit)
    assert fake.calls == [0, 1]
    assert fake.audits == [{"outcome": "changed", "count": 1, "matching_count": 3}]
    outage = DatabaseError("gone")
    fake.outcomes = {2: outage}
    with pytest.raises(DatabaseError):
        apply(preview)
