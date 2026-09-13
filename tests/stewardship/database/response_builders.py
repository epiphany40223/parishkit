"""Source-backed Family sessions for the real baseline/response owner tests."""

from dataclasses import dataclass, replace
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.accounts.family_authentication import FamilyRuntime
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.lifecycle import CampaignWorkKind
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.rehearsals import code_context, prepare_rehearsals
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease

from ..test_source_corpus import source
from .campaign_builders import add_draft, campaign_clock
from .credential_builders import keys
from .test_family_auth_postgresql import login
from .test_source_families_postgresql import prepare, promote


@dataclass
class ResponseHarness:
    """A real source promotion and authenticated request; no operational credentials."""

    campaign: object
    rings: object
    service: FamilyRuntime
    snapshot: object
    client: object
    request: object
    code: str


def response_source():
    """A valid no-change census fixture, without the corpus test's bad email."""
    data = source()
    data.members[3]["emailAddress"] = "valid@example.org"
    return data


@pytest.fixture
def live_response_service(response_service):
    """Activate through real rehearsal cleanup and token/lifecycle services."""
    from parishkit.stewardship.campaigns.family_identity import (
        code_context as live_code_context,
    )
    from parishkit.stewardship.campaigns.lifecycle import Action
    from parishkit.stewardship.campaigns.rehearsals import (
        cleanup_rehearsal,
        invalidate_rehearsal,
    )

    from .campaign_builders import command

    harness = response_service
    epoch = invalidate_rehearsal(
        campaign_id=harness.campaign.pk, admit=lambda *args: True
    )
    while cleanup_rehearsal(epoch):
        pass
    command(harness.campaign, uuid4(), Action.ACTIVATE)
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
    code = harness.rings.general.decrypt(
        family.code_ciphertext, context=live_code_context(family.pk)
    ).decode()
    client, response = login(code)
    assert response.status_code == 302
    harness.campaign.refresh_from_db()
    return replace(harness, code=code, client=client, request=response.wsgi_request)


@pytest.fixture
def response_service(auth_service, settings):
    """Promote coherent eligibility before issuing rehearsal credentials."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    _, row, _ = add_draft(auth_service.store, auth_service.store.active(), uuid4())
    campaign = Campaign.objects.get(pk=UUID(row["id"]))
    rings = keys()
    snapshot, claim = prepare(response_source())
    snapshot = promote(snapshot, claim, campaign, rings)
    service = FamilyRuntime(
        auth_service.store, auth_service.limiter, rings.general, rings.mac, rings.public
    )
    settings.STEWARDSHIP_FAMILY_RUNTIME = service
    with campaign_clock(campaign.active_configuration.starts_at):
        prepare_rehearsals(
            campaign_id=campaign.pk,
            family_ids=list(
                FamilyCampaign.objects.filter(portal_eligible=True).values_list(
                    "pk", flat=True
                )
            ),
            general=rings.general,
            mac=rings.mac,
            public=rings.public,
            purpose=CampaignWorkKind.REHEARSAL,
            admit=lambda *args: True,
        )
        credential = RehearsalCredential.objects.get()
        code = rings.general.decrypt(
            credential.code_ciphertext, context=code_context(credential.pk)
        ).decode()
        client, response = login(code)
        assert response.status_code == 302
        yield ResponseHarness(
            campaign, rings, service, snapshot, client, response.wsgi_request, code
        )
