"""The load check's reads run under the real web login inside read-only guards."""

import json

import pytest

from parishkit.stewardship import load_check
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.responses.models import FamilyFormBaseline
from parishkit.stewardship.runtime_budget import RuntimeBudget
from parishkit.stewardship.source.models import SourceSnapshotPin

from .test_background_grants_postgresql import task_login
from .test_financial_source_postgresql import financial_source

pytestmark = pytest.mark.django_db(transaction=True)


def test_measurements_use_web_grants_and_write_nothing(response_service):
    """A tiny promoted population is measured completely with the web's authority.

    The fixture's second Family is inactive, so it proves the sample covers only
    Families the form can serve while the population still counts every row.
    """
    harness = response_service
    rows = FamilyCampaign.objects.filter(campaign=harness.campaign)
    families, eligible = rows.count(), rows.filter(portal_eligible=True).count()
    assert families > eligible >= 1
    audits, pins = AuditEvent.objects.count(), SourceSnapshotPin.objects.count()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        document = load_check.measure(RuntimeBudget(), samples=3, concurrency=2)
    load_check.safe_document(document)
    # The document passed the vocabulary check above, so it is safe evidence.
    evidence = json.dumps(document, sort_keys=True)
    assert document["check"] == "load" and document["result"] == "pass", evidence
    population = document["population"]
    assert population["family_campaign_rows"] == families
    assert population["portal_eligible_families"] == eligible
    assert population["active_families"] == 1
    assert population["reference_families"] == 5000
    form = document["family_form_inputs"]
    assert form["samples"] == min(3, eligible) and form["concurrency"] == 2
    assert form["failures"] == 0 and form["serial"]["runs"] == form["samples"]
    assert form["concurrent"]["runs"] == form["samples"]
    reports = document["reports"]
    assert reports["statistics"]["runs"] == 20 and reports["statistics"]["pass"]
    assert reports["information_first_page"]["pass"]
    assert reports["financial_first_page"] == {"status": "skipped"}
    assert document["invitation_run"] == {"status": "not_run"}
    assert document["runtime_budget"]["web_threads"] == RuntimeBudget().web_threads
    assert set(document["background"]) == {"start", "end"}
    # Read-only guards: no baseline, pin, outbox row or audit event was created.
    assert not FamilyFormBaseline.objects.exists()
    assert not OutboxMessage.objects.exists()
    assert (AuditEvent.objects.count(), SourceSnapshotPin.objects.count()) == (
        audits,
        pins,
    )


def test_financial_first_page_is_measured_with_a_synthetic_administrator(
    response_service,
):
    """With the financial module enabled, the money page is timed, not skipped.

    The SQL projection binds only the campaign, filters, proof and page, so a
    synthetic Administrator principal is enough; no session is involved.
    """
    harness = response_service
    financial_source(harness)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        document = load_check.measure(RuntimeBudget(), samples=1, concurrency=1)
    load_check.safe_document(document)
    evidence = json.dumps(document, sort_keys=True)
    financial = document["reports"]["financial_first_page"]
    assert financial["runs"] == 20 and financial["failures"] == 0, evidence
    assert financial["pass"] and document["result"] == "pass", evidence
    assert not FamilyFormBaseline.objects.exists()
