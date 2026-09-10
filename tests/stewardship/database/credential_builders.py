"""Synthetic key material over the real installer/campaign and credential schema."""

from dataclasses import dataclass
from uuid import uuid5

from django.db import transaction

from parishkit.stewardship.accounts.cryptography import (
    CodeMacKeyring,
    GeneralKeyring,
    Key,
    TokenPrivateKeyring,
)
from parishkit.stewardship.campaigns.credential_keys import initialize_key_inventories
from parishkit.stewardship.campaigns.family_identity import (
    FamilyStatus,
    reconcile_families,
)

from .campaign_builders import draft_campaign


@dataclass(frozen=True)
class TestKeys:
    """Test-only service bundle; web production never gets the private component."""

    __test__ = False
    general: GeneralKeyring
    mac: CodeMacKeyring
    private: TokenPrivateKeyring

    @property
    def public(self):
        return self.private.public()


def keys():
    """Initialize key-coherence records with unrelated deterministic test keys."""
    result = TestKeys(
        GeneralKeyring([Key("g1", "active", b"g" * 32)]),
        CodeMacKeyring([Key("m1", "active", b"m" * 32)]),
        TokenPrivateKeyring([Key("t1", "active", b"t" * 32)]),
    )
    initialize_key_inventories(result.general, result.mac, result.public)
    return result


def admitted_population(campaign):
    """Storage integration test only; real source-promotion admission is DAT-03."""
    assert campaign.state in {"draft", "scheduled", "active"}
    return True


def populate(campaign, ring, statuses=None, *, generation=1):
    """Outer atomic transaction mirrors the future immutable snapshot promotion."""
    statuses = (
        [FamilyStatus(1, True, True, True, True)] if statuses is None else statuses
    )
    with transaction.atomic():
        return reconcile_families(
            campaign_id=campaign.pk,
            source_snapshot_id=uuid5(campaign.pk, str(generation)),
            source_generation=generation,
            statuses=statuses,
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
            admit=admitted_population,
        )


def family_campaign(tmp_path, *, count=1):
    """Create stable campaign identities through their actual bulk allocator."""
    store, campaign, actor = draft_campaign(tmp_path)
    ring = keys()
    populate(
        campaign,
        ring,
        [FamilyStatus(n + 1, True, True, True, True) for n in range(count)],
    )
    return store, campaign, actor, ring
