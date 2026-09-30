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
@pytest.mark.parametrize("failure", [None, "query", ValueError, TypeError])
def test_role_bound_code_report_and_safe_audit(
    auth_service, google, settings, role, expected, failure, monkeypatch
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
        if failure in (ValueError, TypeError):

            def broken(*args, **kwargs):
                raise failure("private server-side formatting error")

            monkeypatch.setattr(
                "parishkit.stewardship.accounts.code_reports.render_to_string", broken
            )
        response = browser.get(
            f"/admin/campaign/{campaign.pk}/family-codes",
            data={"page": "invalid"} if failure == "query" else {},
            **{"gunicorn.socket": server},
        )
        if expected == 200 and failure is not None:
            assert response.status_code == (400 if failure == "query" else 503)
            assert b"private server-side" not in response.content
            if failure == "query":
                assert b"Invalid request" in response.content
                assert not AuditEvent.objects.filter(
                    event_type="family_codes_viewed"
                ).exists()
            else:
                assert response["Retry-After"] == "5"
                outcomes = list(
                    AuditContext.objects.filter(event__event_type="family_codes_viewed")
                    .order_by("event__created_at")
                    .values_list("context", flat=True)
                )
                assert outcomes == [
                    {"outcome": "started"},
                    {"outcome": "failed", "count": 0},
                ]
            return
        assert response.status_code == expected
        if expected == 200:
            body = b"".join(response.streaming_content)
            assert code in body
            # The shared navigator pages the report with a bounded total.
            assert b'class="table-nav"' in body and b"Rows per page" in body
            assert b'name="size"' in body and b"Page 1 of 1" in body
            response.close()
            events = AuditEvent.objects.filter(
                event_type="family_codes_viewed"
            ).order_by("created_at")
            assert events.count() == 2
            event = events.last()
            assert event.ownership_scope == "parish"
            assert event.campaign_reference == campaign.pk
            contexts = [AuditContext.objects.get(event=item).context for item in events]
            assert contexts == [
                {"outcome": "started"},
                {"outcome": "succeeded", "count": 1},
            ]
            assert code.decode() not in json.dumps(contexts)
        else:
            assert code not in response.content
            assert not AuditEvent.objects.filter(
                event_type="family_codes_viewed"
            ).exists()
    finally:
        server.close()
        client.close()


def test_code_report_sorts_by_duid_on_the_server(auth_service, google, settings):
    """DUID sorts both ways before paging; the encrypted code never sorts."""
    from parishkit.stewardship.campaigns.family_identity import FamilyStatus

    store = auth_service.store
    result, row, _ = add_draft(store, store.active(), store.active().version_id)
    campaign = Campaign.objects.get(pk=row["id"])
    ring = keys()
    populate(
        campaign, ring, [FamilyStatus(n, True, True, True, True) for n in (3, 1, 2)]
    )
    settings.STEWARDSHIP_FAMILY_RUNTIME = FamilyRuntime(
        store, auth_service.limiter, ring.general, ring.mac, ring.public
    )
    browser, _ = signed_in()
    path = f"/admin/campaign/{campaign.pk}/family-codes"

    def duids(**query):
        """The DUIDs one page lists, in order."""
        server, client = socket.socketpair()
        try:
            response = browser.get(path, data=query, **{"gunicorn.socket": server})
            assert response.status_code == 200
            body = b"".join(response.streaming_content).decode()
            response.close()
        finally:
            server.close()
            client.close()
        return [
            int(line.split('class="numeric">')[1].split("<")[0])
            for line in body.split('<th scope="row"')[1:]
        ], body

    assert duids()[0] == [1, 2, 3]
    listed, body = duids(sort="-duid", size="25")
    assert listed == [3, 2, 1] and 'aria-sort="descending"' in body
    assert "Page 1 of 1" in body and "Showing 1–3 of 3" in body
    assert duids(sort="-duid", size="25", page="1")[0] == [3, 2, 1]
    for token in ("code", "family_duid", "-id"):
        server, client = socket.socketpair()
        try:
            refused = browser.get(
                path, data={"sort": token}, **{"gunicorn.socket": server}
            )
            assert refused.status_code == 400
        finally:
            server.close()
            client.close()
