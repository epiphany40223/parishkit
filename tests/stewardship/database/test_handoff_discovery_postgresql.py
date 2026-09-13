"""Public-key discovery with actual target and web PostgreSQL identities."""

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import Key
from parishkit.stewardship.accounts.handoff_discovery import (
    public_handoff,
    publish_handoff,
)
from parishkit.stewardship.accounts.handoff_models import PublicCredentialHandoff

from .test_credential_isolation_postgresql import identity, isolated_roles  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_public_handoff_row_security_applies_to_schema_owner():
    """Owner-scoped SELECT policies must not be bypassed by table ownership."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE oid='public.stewardship_public_credential_handoff'::regclass"
        )
        assert cursor.fetchone() == (True, True)


@pytest.fixture
def handoff_roles(request):
    """Extend only disposable fixture roles with the exact public discovery grants."""
    request.getfixturevalue("isolated_roles")
    with connection.cursor() as cursor:
        cursor.execute(
            "GRANT SELECT ON stewardship_public_credential_handoff "
            "TO pk_stewardship_web"
        )
        for role in (
            "pk_stewardship_credential_slack",
            "pk_stewardship_credential_parishsoft",
        ):
            cursor.execute(
                "GRANT SELECT, INSERT ON stewardship_public_credential_handoff "
                f'TO "{role}"'
            )


def key(target="slack", *, material=b"s" * 32):
    """Fixed fake private bytes; publication must derive a different public key."""
    return PrivateHandoff(target, Key("synthetic-handoff", "active", material))


def test_web_discovers_encryption_only_and_target_can_open_it(handoff_roles):
    """Repeated startup publishes once; the browser-side type cannot decrypt."""
    private = key()
    with identity("pk_stewardship_credential_slack"):
        first = publish_handoff(private)
        assert publish_handoff(private) == first
    assert PublicCredentialHandoff.objects.count() == 1
    stored = bytes(PublicCredentialHandoff.objects.get().public_key)
    assert stored == private.public().key.material and stored != private.key.material
    request = uuid4()
    with identity("pk_stewardship_web"):
        public = public_handoff("slack")
        assert not hasattr(public, "open")
        sealed = public.seal(request, b"synthetic-replacement")
        with pytest.raises(ConfigError):
            publish_handoff(private)
    assert private.open(request, sealed) == b"synthetic-replacement"


def test_public_handoff_cannot_be_replaced_or_cross_target_published(
    handoff_roles, caplog
):
    """Neither a role flag nor a known key identity is cross-target authority."""
    with identity("pk_stewardship_credential_slack"):
        publish_handoff(key())
        with pytest.raises(ConfigError):
            publish_handoff(key(material=b"x" * 32))
        assert "credential_handoff_key_mismatch" in caplog.text
        assert (b"x" * 32).decode() not in caplog.text
        with pytest.raises(ConfigError):
            publish_handoff(key("parishsoft"))
        with pytest.raises(DatabaseError), transaction.atomic():
            PublicCredentialHandoff.objects.create(
                target="parishsoft", key_id="foreign", public_key=b"x" * 32
            )
    with identity("pk_stewardship_credential_parishsoft"):
        assert not PublicCredentialHandoff.objects.filter(target="slack").exists()
        publish_handoff(key("parishsoft", material=b"p" * 32))
    with identity("pk_stewardship_web"):
        assert PublicCredentialHandoff.objects.count() == 2
    with (
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_public_credential_handoff SET public_key=%s",
            [b"z" * 32],
        )
    with (
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_public_credential_handoff")


@pytest.mark.parametrize(
    "values",
    [
        {"public_key": b"short"},
        {"key_id": "bad label"},
        {"actor_id": uuid4()},
    ],
)
def test_raw_publication_rejects_invalid_material_and_spoofed_actor(
    handoff_roles, values
):
    """SQL repeats bounds even when the Python publication service is bypassed."""
    with (
        identity("pk_stewardship_credential_slack"),
        pytest.raises(DatabaseError),
        transaction.atomic(),
    ):
        PublicCredentialHandoff.objects.create(
            **{
                "target": "slack",
                "key_id": "valid",
                "public_key": b"p" * 32,
            }
            | values
        )
    assert not PublicCredentialHandoff.objects.exists()


def test_missing_handoff_does_not_fall_back_to_another_target(handoff_roles):
    """An installer must publish before a replacement can be safely sealed."""
    with identity("pk_stewardship_web"), pytest.raises(ConfigError):
        public_handoff("slack")


def test_bootstrap_cannot_adopt_an_existing_publication(handoff_roles):
    """The reviewed RLS exception grants visibility, not an exemption from emptiness."""
    from parishkit.stewardship.accounts.bootstrap_schema import bootstrap_version
    from parishkit.stewardship.accounts.configuration_snapshots import prepare_snapshot

    with identity("pk_stewardship_credential_slack"):
        publish_handoff(key())
    with pytest.raises(DatabaseError, match="empty application database"):
        prepare_snapshot(
            bootstrap_version(uuid4(), "admin@example.org"),
            actor_id=uuid4(),
            correlation_id=uuid4(),
        )
