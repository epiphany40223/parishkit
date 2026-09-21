"""Which Administrators still see a security event, decided without a database."""

from types import SimpleNamespace

import pytest

from parishkit.stewardship.accounts.security_events import KINDS, cleared

ACTOR, OTHER, NEWCOMER = "admin@example.org", "second@example.org", "new@example.org"


def event(recipients):
    """One expansion recorded at activation with the Administrators of the time."""
    return SimpleNamespace(recipients=recipients)


def acknowledgement(email, *, own=False):
    """One Administrator's acknowledgement, by the address held then."""
    return SimpleNamespace(email=email, own=own)


@pytest.mark.parametrize("viewer", [ACTOR, OTHER, NEWCOMER])
def test_an_unacknowledged_event_shows_for_every_administrator(viewer):
    """Recipients and Administrators who came later alike see it."""
    assert not cleared(event([ACTOR, OTHER]), [], viewer_email=viewer)


def test_the_granting_actor_alone_clears_it_only_for_themselves():
    """Another Administrator existed at activation, so theirs is still awaited."""
    value = event([ACTOR, OTHER])
    own = [acknowledgement(ACTOR, own=True)]
    assert cleared(value, own, viewer_email=ACTOR)
    assert not cleared(value, own, viewer_email=OTHER)
    assert not cleared(value, own, viewer_email=NEWCOMER)


def test_another_recipient_settles_it_for_everyone():
    """One acknowledgement by an Administrator who existed then settles the event."""
    value = event([ACTOR, OTHER])
    other = [acknowledgement(OTHER)]
    for viewer in (ACTOR, OTHER, NEWCOMER):
        assert cleared(value, other, viewer_email=viewer)


def test_a_newer_administrator_clears_it_only_for_themselves():
    """The granted account, or any later Administrator, cannot wave it through."""
    value = event([ACTOR, OTHER])
    newcomer = [acknowledgement(NEWCOMER)]
    assert cleared(value, newcomer, viewer_email=NEWCOMER)
    assert not cleared(value, newcomer, viewer_email=ACTOR)
    assert not cleared(value, newcomer, viewer_email=OTHER)
    # Even together with the actor's own, the other recipient is still awaited.
    both = [*newcomer, acknowledgement(ACTOR, own=True)]
    assert not cleared(value, both, viewer_email=OTHER)


def test_the_only_administrator_settles_it_alone():
    """With no other Administrator at activation, the actor's own word suffices."""
    value = event([ACTOR])
    own = [acknowledgement(ACTOR, own=True)]
    for viewer in (ACTOR, NEWCOMER):
        assert cleared(value, own, viewer_email=viewer)


def test_a_recovery_event_is_settled_by_any_recipient():
    """Recovery has no portal actor, so no acknowledgement is the actor's own."""
    value = event([ACTOR])
    assert not cleared(value, [], viewer_email=ACTOR)
    assert cleared(value, [acknowledgement(ACTOR)], viewer_email=OTHER)


def test_every_expansion_kind_the_trigger_records_has_a_label():
    """The dashboard words each of the specification's three expansions."""
    assert set(KINDS) == {
        "administrator_granted",
        "domain_created",
        "domain_staff_granted",
    }
