"""Actual HTTP code access enforces current Staff scope and response-lifetime guard."""

import json
import socket

import pytest

from parishkit.stewardship.accounts.family_authentication import FamilyRuntime
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.models import Campaign, FamilyCampaign

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import add_draft, change
from .credential_builders import keys, populate

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "role,expected", [("administrator", 200), ("staff", 200), ("ministry_leader", 403)]
)
def test_role_bound_code_report_and_safe_audit(
    auth_service, google, settings, role, expected
):
    """Testing reports retain Production codes rather than rehearsal credentials."""
    store = auth_service.store
    actor = store.active().version_id
    if role != "administrator":
        row = address("reader@example.org", roles=(role,))
        assert (
            change(
                store,
                store.active(),
                actor,
                [{"operation": "add", "section": "login_rules", **row}],
            ).state
            == "applied"
        )
        google[0]["email"] = "reader@example.org"
    result, row, _ = add_draft(store, store.active(), actor)
    assert result.state == "applied"
    campaign = Campaign.objects.get(pk=row["id"])
    ring = keys()
    populate(campaign, ring)
    settings.STEWARDSHIP_FAMILY_RUNTIME = FamilyRuntime(
        store, auth_service.limiter, ring.general, ring.mac, ring.public
    )
    family = FamilyCampaign.objects.get()
    code = ring.general.decrypt(family.code_ciphertext, context=code_context(family.pk))
    browser, signed = signed_in()
    assert signed.status_code == 302
    server, client = socket.socketpair()
    try:
        response = browser.get(
            f"/admin/campaign/{campaign.pk}/family-codes", **{"gunicorn.socket": server}
        )
        assert response.status_code == expected
        if expected == 200:
            assert code in b"".join(response.streaming_content)
            response.close()
            event = AuditEvent.objects.get(event_type="family_codes_viewed")
            assert event.ownership_scope == "parish"
            assert event.campaign_reference == campaign.pk
            assert code.decode() not in json.dumps(
                AuditContext.objects.get(event=event).context
            )
        else:
            assert code not in response.content
            assert not AuditEvent.objects.filter(
                event_type="family_codes_viewed"
            ).exists()
    finally:
        server.close()
        client.close()
