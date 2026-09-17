"""Production code candidates stay key-bounded before current Family admission."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.cryptography import CodeMacKeyring, Key
from parishkit.stewardship.accounts.family_authentication import FamilyRuntime, lookup
from parishkit.stewardship.campaigns.credential_keys import add_rotation_key
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    FamilyCodeFingerprint,
)
from parishkit.stewardship.campaigns.family_identity import FamilyStatus, code_context
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.mac_rotation import backfill_mac_batch
from parishkit.stewardship.campaigns.models import Campaign

from .campaign_builders import add_draft, campaign_clock, command
from .credential_builders import keys, populate
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def production_codes(auth_service):
    """Two Families and two legitimate lookup keys via real lifecycle owners."""
    _, row, _ = add_draft(auth_service.store, auth_service.store.active(), uuid4())
    campaign = Campaign.objects.get(pk=UUID(row["id"]))
    ring = keys()
    replacement = CodeMacKeyring(
        [Key("m1", "lookup-only", b"m" * 32), Key("m2", "active", b"n" * 32)]
    )
    populate(
        campaign,
        ring,
        [FamilyStatus(index, True, True, True, True) for index in (1, 2)],
    )
    family = FamilyCampaign.objects.get(campaign=campaign, family_duid=1)
    code = ring.general.decrypt(
        family.code_ciphertext, context=code_context(family.pk)
    ).decode()
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, uuid4(), Action.ACTIVATE)
        add_rotation_key(ring.mac, replacement)
        ring = replace(ring, mac=replacement)
        assert (
            backfill_mac_batch(
                campaign_id=campaign.pk,
                general=ring.general,
                mac=ring.mac,
                admit=lambda: True,
            )
            == 2
        )
        service = FamilyRuntime(
            auth_service.store,
            auth_service.limiter,
            ring.general,
            ring.mac,
            ring.public,
        )
        yield campaign, ring, service, family, code


def test_mac_query_has_no_population_join_and_duplicate_keys_count_once(
    production_codes,
):
    """Rotation matches the same Family twice without ambiguous or broad reads."""
    campaign, ring, service, family, code = production_codes
    assert FamilyCodeFingerprint.objects.filter(family=family).count() == 2
    with web_login(), CaptureQueriesContext(connection) as queries:
        result = lookup(service, code=code)
    assert result[0] == family.pk and result[2] == "production"
    mac_queries = [
        row["sql"]
        for row in queries
        if 'FROM "stewardship_family_code_mac"' in row["sql"]
    ]
    assert len(mac_queries) == 1
    assert " JOIN " not in mac_queries[0]
    assert '"stewardship_family_campaign"' not in mac_queries[0]
    assert '"campaign_id"' in mac_queries[0] and '"digest"' in mac_queries[0]
    eligibility_queries = [
        row["sql"]
        for row in queries
        if 'FROM "stewardship_family_campaign"' in row["sql"]
    ]
    assert len(eligibility_queries) == 1
    assert '"stewardship_family_campaign"."id" IN (' in eligibility_queries[0]
    assert '"stewardship_family_code_mac"' not in eligibility_queries[0]
    assert " JOIN " not in eligibility_queries[0]


@pytest.mark.parametrize("other_eligible", [False, True])
def test_distinct_key_candidates_are_filtered_before_ambiguity_check(
    production_codes, monkeypatch, other_eligible
):
    """Synthetic cross-key collision denies two active matches, not one inactive."""
    campaign, ring, service, family, code = production_codes
    fingerprints = dict(
        FamilyCodeFingerprint.objects.filter(
            campaign=campaign, key_id="m1", family=family
        ).values_list("key_id", "digest")
    )
    fingerprints.update(
        FamilyCodeFingerprint.objects.filter(
            campaign=campaign, key_id="m2", family__family_duid=2
        ).values_list("key_id", "digest")
    )
    if not other_eligible:
        populate(
            campaign,
            ring,
            [
                FamilyStatus(1, True, True, True, True),
                FamilyStatus(2, False, False, False, False),
            ],
            generation=2,
        )
    monkeypatch.setattr(CodeMacKeyring, "lookups", lambda *args: fingerprints)
    result = lookup(service, code=code)
    if other_eligible:
        assert result is None
    else:
        assert result[0] == family.pk


def test_current_ineligibility_rejects_retained_code_candidates(production_codes):
    """Retained fingerprints cannot admit a currently ineligible Family."""
    campaign, ring, service, family, code = production_codes
    populate(
        campaign,
        ring,
        [FamilyStatus(1, False, False, False, False)],
        generation=2,
    )
    assert FamilyCodeFingerprint.objects.filter(family=family).count() == 2
    assert lookup(service, code=code) is None
