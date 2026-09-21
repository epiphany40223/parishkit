"""Which Administrators still see a security event, decided without a database."""

from types import SimpleNamespace

import pytest

from parishkit.stewardship.accounts.security_events import KINDS, cleared

ACTOR, OTHER, NEWCOMER = "admin@example.org", "second@example.org", "new@example.org"


def event(recipients, target=NEWCOMER):
    """One expansion recorded at activation with the Administrators of the time."""
    return SimpleNamespace(recipients=recipients, target=target)


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
    """With no other Administrator at activation, the actor's own word suffices.

    The recipient list names the actor's address at activation; an address
    changed since still counts, because the list is judged by its length.
    """
    value = event([ACTOR])
    for own in (
        acknowledgement(ACTOR, own=True),
        acknowledgement("renamed@example.org", own=True),
    ):
        for viewer in (ACTOR, NEWCOMER):
            assert cleared(value, [own], viewer_email=viewer)


def test_an_event_with_no_recipients_is_settled_by_anyone():
    """A root activation had nobody to await, so one acknowledgement suffices."""
    value = event([])
    assert not cleared(value, [], viewer_email=NEWCOMER)
    assert cleared(value, [acknowledgement(NEWCOMER)], viewer_email=OTHER)


def test_a_recovery_event_is_settled_by_a_prior_recipient_never_its_target():
    """Recovery names the account it grants so it is told; it settles nothing."""
    value = event([ACTOR, NEWCOMER], target=NEWCOMER)
    assert not cleared(value, [], viewer_email=ACTOR)
    by_target = [acknowledgement(NEWCOMER)]
    assert cleared(value, by_target, viewer_email=NEWCOMER)
    assert not cleared(value, by_target, viewer_email=ACTOR)
    assert not cleared(value, by_target, viewer_email=OTHER)
    assert cleared(value, [acknowledgement(ACTOR)], viewer_email=OTHER)


def test_a_recovery_with_nobody_else_is_settled_by_its_target():
    """When no Administrator existed before, the granted account settles it."""
    value = event([NEWCOMER], target=NEWCOMER)
    assert cleared(value, [acknowledgement(NEWCOMER)], viewer_email=OTHER)


def test_every_expansion_kind_the_trigger_records_has_a_label():
    """The dashboard words each of the specification's three expansions."""
    assert set(KINDS) == {
        "administrator_granted",
        "domain_created",
        "domain_staff_granted",
    }
