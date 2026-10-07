"""Resolve held messages offers only what the server accepts (#563)."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from parishkit.stewardship.accounts import delivery_control_commands as commands
from parishkit.stewardship.accounts.delivery_control_commands import (
    RESOLVABLE_TYPES,
    _clears_pause,
    resolution_choices,
)
from parishkit.stewardship.storage import StaleRecordError

EMPTY = {"held": 0, "submitting": 0, "unknown": 0}


def current(*, stranded=0, submitting=0, unknown=0, **types):
    """A closed-campaign inventory with the given per-type counts."""
    kinds = {kind: EMPTY | counts for kind, counts in types.items()}
    held = stranded + sum(counts["held"] for counts in kinds.values())
    return {
        "held": held,
        "stranded": stranded,
        "submitting": submitting
        + sum(counts["submitting"] for counts in kinds.values()),
        "unknown": unknown + sum(counts["unknown"] for counts in kinds.values()),
        "types": kinds,
    }


def test_choices_cover_every_resolvable_type():
    """The page lists the same types the server resolves."""
    every = current(**{kind: {"held": 1} for kind in RESOLVABLE_TYPES})
    listed = resolution_choices(every, {"ready": True})["types"]
    assert {item["kind"] for item in listed} == RESOLVABLE_TYPES


def test_only_held_types_are_listed_and_release_needs_the_check():
    """Empty types are left out; Release waits for a ready check."""
    inventory = current(receipt={"held": 2}, daily_digest={"held": 0})
    waiting = resolution_choices(inventory, {"ready": False})
    assert [item["kind"] for item in waiting["types"]] == ["receipt"]
    assert not waiting["release"] and waiting["cancel"] and not waiting["clear"]
    assert resolution_choices(inventory, {"ready": True})["release"]
    assert not resolution_choices(inventory, None)["release"]


@pytest.mark.parametrize("uncertain", [{"submitting": 1}, {"unknown": 1}])
def test_cancel_leaves_out_types_with_uncertain_mail(uncertain):
    """A type with mail being handed over or uncertain cannot be cancelled."""
    inventory = current(receipt={"held": 2} | uncertain, weekly_digest={"held": 1})
    choices = resolution_choices(inventory, {"ready": False})
    assert {item["kind"]: item["cancellable"] for item in choices["types"]} == {
        "receipt": False,
        "weekly_digest": True,
    }
    assert choices["cancel"]
    assert [str(label) for label in choices["uncancellable"]] == ["Submission receipts"]
    alone = resolution_choices(current(receipt={"held": 2} | uncertain), None)
    assert not alone["cancel"]


@pytest.mark.parametrize(
    "inventory,clears",
    [
        (current(stranded=3), True),
        (current(), True),
        (current(stranded=3, unknown=1), False),
        (current(stranded=3, submitting=1), False),
        (current(stranded=3, receipt={"held": 1}), False),
    ],
)
def test_clear_matches_the_server_rule(inventory, clears):
    """Clear is offered exactly when the server would clear with no types."""
    assert resolution_choices(inventory, None)["clear"] is clears
    assert _clears_pause(inventory, []) is clears


INVENTORIES = [
    current(receipt={"held": 2}, weekly_digest={"held": 1}),
    current(receipt={"held": 2, "unknown": 1}, daily_digest={"held": 3}),
    current(daily_digest={"held": 1, "submitting": 1}),
    current(stranded=3),
    current(stranded=3, unknown=1),
]


@pytest.fixture
def server(monkeypatch):
    """Stub the provider check and the coverage query _closed_selection reads,
    so the server's own rules run without a database (nothing is preparing)."""
    state = {"ready": False}
    monkeypatch.setattr(commands, "health", lambda _campaign: dict(state))

    class Cursor:
        """The one coverage row: nothing covered, no report preparing."""

        def execute(self, *_args):
            """Ignore the query; the row below is the answer."""

        def fetchone(self):
            """Return empty coverage with no preparation in progress."""
            return ("{}", False)

    @contextmanager
    def cursor():
        """A context-managed stand-in for connection.cursor()."""
        yield Cursor()

    monkeypatch.setattr(commands, "connection", SimpleNamespace(cursor=cursor))
    return state


@pytest.mark.parametrize("inventory", INVENTORIES)
@pytest.mark.parametrize("ready", [False, True])
def test_every_offered_choice_is_accepted_by_the_server(server, inventory, ready):
    """Each choice the form offers passes _closed_selection; each choice it
    withholds is refused there (#563: an available Preview means accepted)."""
    server["ready"] = ready
    campaign = SimpleNamespace(pk=1)
    choices = resolution_choices(inventory, {"ready": ready})
    for decision in ("release", "cancel", "clear"):
        if decision == "clear":
            selections = [[]]
        else:
            # Each single type the form would show for this decision.
            selections = [
                [item["kind"]]
                for item in choices["types"]
                if decision == "release" or item["cancellable"]
            ]
        for types in selections or [[]]:
            offered = choices[decision] and bool(types or decision == "clear")
            try:
                commands._closed_selection(
                    campaign, inventory, decision=decision, types=types
                )
                accepted = True
            except StaleRecordError:
                accepted = False
            assert accepted is offered, (decision, types)
    # A type the form hides for Cancel is refused there too.
    for item in choices["types"]:
        if not item["cancellable"]:
            with pytest.raises(StaleRecordError):
                commands._closed_selection(
                    campaign, inventory, decision="cancel", types=[item["kind"]]
                )
