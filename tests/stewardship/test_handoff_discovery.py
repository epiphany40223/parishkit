"""Reject public/private type confusion before any discovery database access."""

import pytest

from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import Key
from parishkit.stewardship.accounts.handoff_discovery import (
    public_handoff,
    publish_handoff,
)


@pytest.mark.parametrize("value", [None, "slack", b"private-material"])
def test_publication_requires_the_installer_private_handoff_type(value):
    """Callers cannot accidentally persist supplied bytes as advertised public keys."""
    with pytest.raises(TypeError):
        publish_handoff(value)


def test_publication_refuses_already_public_type():
    """The publication service always derives the actual installer's public key."""
    public = PrivateHandoff("slack", Key("test", "active", b"h" * 32)).public()
    with pytest.raises(TypeError):
        publish_handoff(public)


@pytest.mark.parametrize("value", [None, {}, "unrecognized"])
def test_discovery_rejects_unknown_targets_without_io(value):
    """Target vocabulary cannot become an arbitrary database or file lookup."""
    with pytest.raises(ValueError):
        public_handoff(value)
