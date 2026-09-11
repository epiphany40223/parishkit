"""Integrated review regressions for retained rehearsal and credential authority."""

from uuid import uuid4

import pytest
from django.db.models import F

from parishkit.stewardship.accounts.cryptography import (
    CodeMacKeyring,
    CryptographicError,
    GeneralKeyring,
    Key,
    TokenPrivateKeyring,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.cipher_rotation import (
    reencrypt_batch,
    verify_ciphertexts,
)
from parishkit.stewardship.campaigns.credential_keys import add_rotation_key
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    CredentialKeyState,
    DeploymentCredentialState,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.mac_rotation import (
    backfill_mac_batch,
    collision_only,
)
from parishkit.stewardship.campaigns.rehearsals import (
    code_context,
    release_rehearsal_gate,
    token_context,
)
from parishkit.stewardship.storage import StaleRecordError

from .credential_builders import family_campaign
from .test_link_tokens_postgresql import begin
from .test_rehearsals_postgresql import invalidate, prepare
from .test_taskrun_postgresql import new

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("kind", ["general", "token"])
def test_rehearsal_ciphertexts_participate_in_rotation(tmp_path, kind):
    """Rehearsal rows contribute to counts and preserve plaintext after rotation."""
    _, campaign, _, keys = family_campaign(tmp_path, count=1)
    prepare(campaign, keys)
    row = RehearsalCredential.objects.get()
    if kind == "general":
        original, field, context, total = (
            keys.general,
            "code_ciphertext",
            code_context,
            2,
        )
        replacement = GeneralKeyring(
            [Key("g1", "decrypt-only", b"g" * 32), Key("g2", "active", b"h" * 32)]
        )
        add_rotation_key(original, replacement)
    else:
        original, field, context, total = (
            keys.private,
            "token_ciphertext",
            token_context,
            1,
        )
        replacement = TokenPrivateKeyring(
            [Key("t1", "decrypt-only", b"t" * 32), Key("t2", "active", b"u" * 32)]
        )
        add_rotation_key(keys.public, replacement.public())
    plaintext = original.decrypt(getattr(row, field), context=context(row.pk))
    assert (
        verify_ciphertexts(replacement, admit=lambda: True)[original.active.id] == total
    )
    assert reencrypt_batch(replacement, admit=lambda: True) == total
    assert verify_ciphertexts(replacement, admit=lambda: True) == {
        original.active.id: 0,
        replacement.active.id: total,
    }
    row.refresh_from_db()
    assert (
        replacement.decrypt(getattr(row, field), context=context(row.pk)) == plaintext
    )


def test_noop_mac_demotion_has_no_receipt_or_version_change(tmp_path):
    """An unchanged keyring cannot claim completed demotion of old login keys."""
    _, _, _, keys = family_campaign(tmp_path)
    row = CredentialKeyState.objects.get(kind=keys.mac.kind)
    with pytest.raises(CryptographicError, match="changed key usage"):
        collision_only(keys.mac, keys.mac, admit=lambda: True)
    row.refresh_from_db()
    assert row.version == 1
    assert not AuditEvent.objects.filter(
        event_type="family_mac_migration_verified"
    ).exists()


def test_mac_backfill_and_gate_release_have_single_transition_evidence(tmp_path):
    """Real changes are auditable; exact retries without work create no events."""
    _, campaign, _, keys = family_campaign(tmp_path, count=1)
    prepare(campaign, keys)
    replacement = CodeMacKeyring(
        [Key("m1", "lookup-only", b"m" * 32), Key("m2", "active", b"n" * 32)]
    )
    add_rotation_key(keys.mac, replacement)
    args = dict(
        campaign_id=campaign.pk,
        general=keys.general,
        mac=replacement,
        admit=lambda: True,
    )
    assert backfill_mac_batch(**args) == 2
    assert backfill_mac_batch(**args) == 0
    assert (
        AuditEvent.objects.filter(
            event_type="family_mac_backfilled", subject_id=campaign.pk
        ).count()
        == 1
    )
    invalidate(campaign)
    for _ in range(2):
        release_rehearsal_gate(campaign_id=campaign.pk, admit=lambda _: True)
    assert AuditEvent.objects.filter(event_type="rehearsal_gate_released").count() == 1


@pytest.mark.parametrize("changed", ["epoch", "population"])
def test_token_begin_retry_rechecks_derived_live_inputs(tmp_path, changed):
    """Identical caller intent gets typed staleness when restore/source fences move."""
    _, campaign, actor, keys = family_campaign(tmp_path)
    operation = uuid4()
    begin(campaign, actor, keys, operation_id=operation)
    if changed == "epoch":
        DeploymentCredentialState.objects.update(
            family_link_epoch=uuid4(), version=F("version") + 1
        )
    else:
        CampaignCredentialState.objects.filter(campaign=campaign).update(
            population_dirty=True, version=F("version") + 1
        )
    with pytest.raises(StaleRecordError):
        begin(campaign, actor, keys, operation_id=operation)


@pytest.mark.parametrize("decision", [False, None, 1, "yes"])
def test_task_admission_requires_explicit_true(decision):
    """A false or merely truthy callback cannot commit task or audit metadata."""
    from parishkit.stewardship.jobs.models import TaskRun

    with pytest.raises(PermissionError):
        new(admit=lambda *args: decision)
    assert not TaskRun.objects.exists()
    assert not AuditEvent.objects.exists()
