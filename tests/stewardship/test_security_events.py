"""Which Administrators still see a security event, decided without a database."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.security_events import KINDS, cleared

ACTOR, OTHER, NEWCOMER = uuid4(), uuid4(), uuid4()


def event(recipients, actor_id=ACTOR):
    """One expansion recorded at activation with the Administrators of the time."""
    return SimpleNamespace(actor_id=actor_id, recipients=recipients)


def acknowledgement(actor_id, email):
    """One Administrator's acknowledgement, by the address held then."""
    return SimpleNamespace(actor_id=actor_id, email=email)


@pytest.mark.parametrize("viewer", [ACTOR, OTHER, NEWCOMER])
def test_an_unacknowledged_event_shows_for_every_administrator(viewer):
    """Recipients and Administrators who came later alike see it."""
    assert not cleared(
        event(["admin@example.org", "second@example.org"]), [], viewer_id=viewer
    )


def test_the_granting_actor_alone_clears_it_only_for_themselves():
    """Another Administrator existed at activation, so theirs is still awaited."""
    value = event(["admin@example.org", "second@example.org"])
    own = [acknowledgement(ACTOR, "admin@example.org")]
    assert cleared(value, own, viewer_id=ACTOR)
    assert not cleared(value, own, viewer_id=OTHER)
    assert not cleared(value, own, viewer_id=NEWCOMER)


def test_any_other_administrator_clears_it_for_everyone():
    """One acknowledgement by someone other than the actor settles the event."""
    value = event(["admin@example.org", "second@example.org"])
    other = [acknowledgement(OTHER, "second@example.org")]
    for viewer in (ACTOR, OTHER, NEWCOMER):
        assert cleared(value, other, viewer_id=viewer)


def test_the_only_administrator_clears_it_alone():
    """With no other Administrator at activation, the actor's own word suffices."""
    value = event(["admin@example.org"])
    own = [acknowledgement(ACTOR, "admin@example.org")]
    for viewer in (ACTOR, NEWCOMER):
        assert cleared(value, own, viewer_id=viewer)


def test_an_operator_recovery_event_is_cleared_by_any_administrator():
    """Recovery has no portal actor, so every acknowledgement is another's."""
    value = event(["admin@example.org"], actor_id=None)
    assert not cleared(value, [], viewer_id=ACTOR)
    assert cleared(
        value, [acknowledgement(ACTOR, "admin@example.org")], viewer_id=OTHER
    )


def test_every_expansion_kind_the_trigger_records_has_a_label():
    """The dashboard words each of the specification's three expansions."""
    assert set(KINDS) == {
        "administrator_granted",
        "domain_created",
        "domain_staff_granted",
    }
