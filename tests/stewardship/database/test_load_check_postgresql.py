"""The load check's reads run under the real web login inside read-only guards."""

import json

import pytest

from parishkit.stewardship import load_check
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.rehearsals import invalidate_rehearsal
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.responses.models import FamilyFormBaseline
from parishkit.stewardship.runtime_budget import RuntimeBudget
from parishkit.stewardship.source.models import SourceSnapshotPin

from .test_background_grants_postgresql import task_login
from .test_financial_source_postgresql import financial_source

pytestmark = pytest.mark.django_db(transaction=True)


def head(member, family):
    """One active head makes a synthetic household eligible for the campaign."""
    return dict(
        memberDUID=member,
        familyDUID=family,
        firstName="Head",
        lastName=f"Of{family}",
        memberType="Head",
        memberStatus="Active",
        emailAddress=f"head{family}@example.org",
    )


def measured(harness, **options):
    """Run the measurement under the exact web role and vet its vocabulary."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        document = load_check.measure(RuntimeBudget(), harness.service.store, **options)
    load_check.safe_document(document)
    return document


def test_measurements_use_web_grants_and_write_nothing(response_service):
    """Two eligible households are measured concurrently with the web's authority.

    The fixture's inactive second Family proves the sample covers only Families
    the form can serve while the population still counts every row; the added
    third household gives the concurrent phase two overlapping guarded reads.
    """
    harness = response_service
    financial_source(
        harness,
        extra_families={3: dict(familyDUID=3, registeredOrganizationID=5)},
        extra_members={30: head(30, 3)},
    )
    rows = FamilyCampaign.objects.filter(campaign=harness.campaign)
    families, eligible = rows.count(), rows.filter(portal_eligible=True).count()
    assert families == 3 and eligible == 2
    audits, pins = AuditEvent.objects.count(), SourceSnapshotPin.objects.count()
    document = measured(harness, samples=3, concurrency=2)
    # The document passed the vocabulary check above, so it is safe evidence.
    evidence = json.dumps(document, sort_keys=True)
    assert document["check"] == "load" and document["result"] == "pass", evidence
    population = document["population"]
    assert population["family_campaign_rows"] == 3
    assert population["portal_eligible_families"] == 2
    assert population["active_families"] == 2
    assert population["reference_families"] == 5000
    form = document["family_form_inputs"]
    assert form["samples"] == 2 and form["concurrency"] == 2 and form["threads"] == 2
    for section in (form["serial"], form["concurrent"]):
        assert section["runs"] == 2 and section["pass"], evidence
        assert (section["failures"], section["skipped"], section["not_run"]) == (
            0,
            0,
            0,
        )
    reports = document["reports"]
    for name in ("statistics", "financial_first_page", "information_first_page"):
        assert reports[name]["runs"] == 20 and reports[name]["pass"], evidence
    assert document["invitation_run"] == {"status": "not_run"}
    budget = document["runtime_budget"]
    assert budget["web_threads"] == RuntimeBudget().web_threads
    assert budget["web_connection_headroom"] == load_check.web_headroom(RuntimeBudget())
    assert set(document["background"]) == {"start", "end"}
    # Read-only guards: no baseline, pin, outbox row or audit event was created.
    assert not FamilyFormBaseline.objects.exists()
    assert not OutboxMessage.objects.exists()
    assert (AuditEvent.objects.count(), SourceSnapshotPin.objects.count()) == (
        audits,
        pins,
    )


def test_without_the_financial_module_the_money_page_is_skipped(response_service):
    """A campaign without financial detail skips that page; one Family, one thread."""
    document = measured(response_service, samples=3, concurrency=2)
    assert document["result"] == "pass", json.dumps(document, sort_keys=True)
    assert document["reports"]["financial_first_page"] == {"status": "skipped"}
    form = document["family_form_inputs"]
    assert form["samples"] == 1 and form["concurrency"] == 2 and form["threads"] == 1
    assert document["population"]["portal_eligible_families"] == 1


def test_closed_testing_portal_is_refused_before_any_timing(response_service):
    """Once go-live invalidates the rehearsal, the portal and the check are closed."""
    harness = response_service
    invalidate_rehearsal(campaign_id=harness.campaign.pk, admit=lambda campaign: True)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(load_check.LoadCheckRefused),
    ):
        load_check.measure(
            RuntimeBudget(), harness.service.store, samples=1, concurrency=1
        )
    assert not FamilyFormBaseline.objects.exists()


def test_portal_closing_mid_run_refuses_instead_of_skipping(
    response_service, monkeypatch
):
    """Once the scope recheck fails inside a guard, no verdict is published."""
    harness = response_service
    financial_source(
        harness,
        extra_families={3: dict(familyDUID=3, registeredOrganizationID=5)},
        extra_members={30: head(30, 3)},
    )
    real, answers = load_check.portal_open, []

    def closing(store, campaign_id):
        """The portal answers open once, then closed, through the real query."""
        answers.append(real(store, campaign_id))
        return answers[-1] and len(answers) == 1

    monkeypatch.setattr(load_check, "portal_open", closing)
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(load_check.PortalClosed),
    ):
        load_check.measure(
            RuntimeBudget(), harness.service.store, samples=2, concurrency=2
        )
    assert answers == [True, True]
    assert not FamilyFormBaseline.objects.exists()


def test_one_family_losing_eligibility_is_skipped_under_the_half_rule(
    response_service, monkeypatch
):
    """Losing one of two Families is a skip; the run still measures and passes."""
    harness = response_service
    financial_source(
        harness,
        extra_families={3: dict(familyDUID=3, registeredOrganizationID=5)},
        extra_members={30: head(30, 3)},
    )
    real = load_check.family_admitted
    monkeypatch.setattr(
        load_check,
        "family_admitted",
        lambda campaign_id, duid: duid != 3 and real(campaign_id, duid),
    )
    document = measured(harness, samples=2, concurrency=2)
    form = document["family_form_inputs"]
    for section in (form["serial"], form["concurrent"]):
        assert (section["runs"], section["skipped"], section["failures"]) == (2, 1, 0)
        assert section["pass"]
    assert document["result"] == "pass", json.dumps(document, sort_keys=True)
