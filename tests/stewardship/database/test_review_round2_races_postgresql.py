"""Real independent-connection evidence for the second review's locking fixes."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.db import OperationalError, connection, connections, transaction
from django.db.models import F

from parishkit.stewardship.accounts.cryptography import new_token, token_digest
from parishkit.stewardship.campaigns.cipher_rotation import reencrypt_batch
from parishkit.stewardship.campaigns.credential_keys import inventory_digest
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyAccessToken,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.family_identity import (
    FamilyStatus,
    reconcile_families,
)
from parishkit.stewardship.campaigns.key_retirement import RetirementProof, retire_keys
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.link_tokens import (
    prepare_generation_batch,
    token_context,
)
from parishkit.stewardship.campaigns.rehearsals import (
    invalidate_rehearsal,
    release_rehearsal_gate,
)

from .campaign_builders import campaign_clock, change, command
from .credential_builders import family_campaign
from .test_cipher_rotation_postgresql import admitted
from .test_key_retirement_postgresql import rotated_general
from .test_link_tokens_postgresql import begin

pytestmark = pytest.mark.django_db(transaction=True)


def try_owner_lock():
    """Inspect a synthetic transaction lock from an independent backend."""
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(736229,1)")
            return cursor.fetchone()[0]
    finally:
        connections.close_all()


def test_retirement_owner_lock_survives_proof_exit_until_commit(tmp_path):
    """The owner can acquire SQL locks and competing work waits for actual commit."""
    old, new = rotated_general(tmp_path)
    reencrypt_batch(old, admit=admitted)
    observations = []
    with ThreadPoolExecutor(max_workers=1) as executor:

        @contextmanager
        def dependencies(previous, replacement):
            """An actual transaction-scoped lock fences this synthetic evidence."""
            assert connection.in_atomic_block
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(736229,1)")
            transaction.on_commit(
                lambda: observations.append(
                    executor.submit(try_owner_lock).result(timeout=5)
                )
            )
            yield RetirementProof(
                inventory_digest(previous), inventory_digest(replacement), True, True
            )
            assert not executor.submit(try_owner_lock).result(timeout=5)

        retire_keys(old, new, admit=admitted, dependencies=dependencies)
    assert observations == [True]


def change_family_with_short_lock_timeout(identifier):
    """Raw-like ORM mutation bypasses source admission but still meets SQL guards."""
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout='200ms'")
            FamilyCampaign.objects.filter(pk=identifier).update(
                portal_eligible=False, version=F("version") + 1
            )
    finally:
        connections.close_all()


def test_dirty_population_still_serializes_raw_family_changes(tmp_path):
    """An already-dirty flag cannot exempt a writer from the manifest row lock."""
    _, campaign, _, _ = family_campaign(tmp_path)
    family = FamilyCampaign.objects.get()
    CampaignCredentialState.objects.filter(campaign=campaign).update(
        population_dirty=True, version=F("version") + 1
    )
    with ThreadPoolExecutor(max_workers=1) as executor, transaction.atomic():
        CampaignCredentialState.objects.select_for_update().get(campaign=campaign)
        future = executor.submit(change_family_with_short_lock_timeout, family.pk)
        with pytest.raises(OperationalError, match="lock timeout"):
            future.result(timeout=5)


def test_raw_token_issuance_pins_family_eligibility(tmp_path):
    """A foreign-key key-share lock alone would not block an eligibility UPDATE."""
    _, campaign, actor, ring = family_campaign(tmp_path)
    generation = begin(campaign, actor, ring)
    family = FamilyCampaign.objects.get()
    identifier, value = uuid4(), new_token()
    with ThreadPoolExecutor(max_workers=1) as executor, transaction.atomic():
        FamilyAccessToken.objects.create(
            id=identifier,
            family=family,
            campaign=campaign,
            generation=generation,
            ciphertext=ring.public.encrypt(
                value.encode(), context=token_context(identifier)
            ),
            digest=token_digest(value, campaign.pk),
        )
        future = executor.submit(change_family_with_short_lock_timeout, family.pk)
        with pytest.raises(OperationalError, match="lock timeout"):
            future.result(timeout=5)


def test_token_pointer_activation_clears_gate_and_advances_version(tmp_path):
    """Only a pointer transition releases the gate, not unrelated campaign writes."""
    store, campaign, actor, _ = family_campaign(tmp_path)
    invalidate_rehearsal(campaign_id=campaign.pk, admit=lambda *_: True)
    scope = CampaignCredentialState.objects.get(campaign=campaign)
    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "update",
                    "section": "campaigns",
                    "id": str(campaign.pk),
                    "values": {"name": "Renamed campaign"},
                }
            ],
        ).state
        == "applied"
    )
    campaign.refresh_from_db()
    scope.refresh_from_db()
    assert scope.go_live_gate
    before = scope.version
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    scope.refresh_from_db()
    assert not scope.go_live_gate and scope.version > before


@pytest.mark.parametrize("decision", [False, None])
def test_population_admission_requires_explicit_true(tmp_path, decision):
    """A callback using the wrong convention fails closed before population writes."""
    _, campaign, _, ring = family_campaign(tmp_path, count=0)
    with transaction.atomic(), pytest.raises(PermissionError, match="not admitted"):
        reconcile_families(
            campaign_id=campaign.pk,
            source_snapshot_id=uuid4(),
            source_generation=2,
            statuses=[FamilyStatus(1, True, True, True, True)],
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
            admit=lambda *_: decision,
        )
    assert not FamilyCampaign.objects.exists()


@pytest.mark.parametrize("decision", [False, None])
def test_token_and_rehearsal_admissions_reject_wrong_callback_convention(
    tmp_path, decision
):
    """Credential owner callbacks never silently interpret a false return as consent."""
    _, campaign, actor, ring = family_campaign(tmp_path)
    generation = begin(campaign, actor, ring)
    with pytest.raises(PermissionError, match="not admitted"):
        prepare_generation_batch(
            generation_id=generation.pk, public=ring.public, admit=lambda *_: decision
        )
    for operation in (invalidate_rehearsal, release_rehearsal_gate):
        with pytest.raises(PermissionError, match="not admitted"):
            operation(campaign_id=campaign.pk, admit=lambda *_: decision)
    assert not FamilyAccessToken.objects.exists()
