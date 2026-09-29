"""The source form scan reads under the real web login and writes nothing."""

import pytest

from parishkit.stewardship import source_form_check
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.models import FamilyFormBaseline
from parishkit.stewardship.source.models import SourceSnapshotPin

from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def scanned():
    """Run the scan with exactly the web role's grants, as the command does."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return source_form_check.scan()


def test_scan_reports_refused_member_values_by_identifier_only(response_service):
    """A clean source passes; a promoted malformed name is found, never printed."""
    harness = response_service
    family = (
        FamilyCampaign.objects.filter(campaign=harness.campaign, portal_eligible=True)
        .values_list("family_duid", flat=True)
        .get()
    )
    audits, pins = AuditEvent.objects.count(), SourceSnapshotPin.objects.count()
    clean = scanned()
    assert clean == {
        "check": "source_form",
        "portal_eligible_families": 1,
        "families_refused": 0,
        "findings": [],
        "result": "clean",
    }
    assert (AuditEvent.objects.count(), SourceSnapshotPin.objects.count()) == (
        audits,
        pins,
    )
    data = response_source()
    data.members[3].update(firstName="x" * 101, lastName="private\tvalue")
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    audits, pins = AuditEvent.objects.count(), SourceSnapshotPin.objects.count()
    document = scanned()
    assert document["result"] == "findings"
    assert document["families_refused"] == 1
    assert document["findings"] == [
        {
            "family_duid": family,
            "member_duid": 3,
            "field": "first_name",
            "kind": "value",
        },
        {
            "family_duid": family,
            "member_duid": 3,
            "field": "last_name",
            "kind": "value",
        },
    ]
    assert "private" not in str(document)
    # Read-only: no baseline, pin or audit event was created by the scan.
    assert not FamilyFormBaseline.objects.exists()
    assert (AuditEvent.objects.count(), SourceSnapshotPin.objects.count()) == (
        audits,
        pins,
    )
