"""The Family's contact details on its Additional information item (#1595)."""

from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.information_contacts import family_contacts
from parishkit.stewardship.responses.models import AdditionalInformationItem

from ..policy_factory import address
from . import response_builders
from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_report_workspace_postgresql import read
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)

# What the shared source fixture (test_source_corpus.source, with
# response_source's valid head address) holds for Family 1: the Family
# record's own address, and its one head's unpublished address and home
# phone.
FAMILY_EMAIL = b"family@example.org"
HEAD_EMAIL = b"valid@example.org"


def _sign_in_as(harness, google, roles):
    """Sign a fresh browser in as reader@example.org holding ``roles``."""
    store = harness.service.store
    rule = address("reader@example.org", roles=roles)
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0]["email"] = "reader@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    return browser


def _viewed():
    """The ids of every ``information_viewed`` audit event so far."""
    return set(
        AuditEvent.objects.filter(event_type="information_viewed").values_list(
            "pk", flat=True
        )
    )


def test_staff_and_administrators_see_operational_contact(
    live_response_service, google
):
    """The item page lists the Family record's and its head's contact values.

    The head's email is not published in ParishSoft, so its showing proves
    the details are operational. The item view is audited as before, with
    no contact value in the audit, and the queue shows none of them.
    """
    harness = live_response_service
    respond(harness, "Please call us")
    item = AdditionalInformationItem.objects.get()
    detail = reverse("admin:information_item", args=[item.pk])
    administrator, _ = signed_in()
    staff = _sign_in_as(harness, google, ("staff",))
    for browser in (administrator, staff):
        before = _viewed()
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response, body = read(browser, detail)
        assert response.status_code == 200
        section = body.split(b'id="information-contact"', 1)[1].split(b"</section>")[0]
        assert b"<h3>Family record</h3>" in section
        assert b"<h3>Member Example</h3>" in section
        assert FAMILY_EMAIL in section and HEAD_EMAIL in section
        assert b"Home: +1 (202) 555-0123" in section
        # The Family record has no phone in the fixture.
        assert section.count(b"None recorded") == 1
        audited = AuditContext.objects.filter(
            event__event_type="information_viewed"
        ).exclude(event__in=before)
        contexts = list(audited.values_list("context", flat=True))
        assert sorted(context["outcome"] for context in contexts) == [
            "started",
            "succeeded",
        ]
        assert all(context == {"outcome": context["outcome"]} for context in contexts)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(staff, reverse("admin:information_queue"))
    assert response.status_code == 200 and b"Please call us" in body
    assert FAMILY_EMAIL not in body and HEAD_EMAIL not in body
    assert b'id="information-contact"' not in body


def test_ministry_leaders_get_no_contact_details(live_response_service, google):
    """A Ministry leader is refused the item page before any detail is read."""
    harness = live_response_service
    respond(harness, "Leader must not see")
    item = AdditionalInformationItem.objects.get()
    leader = _sign_in_as(harness, google, ("ministry_leader",))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(leader, reverse("admin:information_item", args=[item.pk]))
    assert response.status_code == 403
    assert FAMILY_EMAIL not in body and HEAD_EMAIL not in body
    assert b"Leader must not see" not in body


def test_contact_read_states_and_capability(live_response_service):
    """One statement; missing Families and unusable data say so; scope is rechecked."""
    harness = live_response_service
    snapshot = harness.snapshot.pk
    staff = Principal(uuid4(), frozenset({"staff"}))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        with CaptureQueriesContext(connection) as queries:
            shown = family_contacts(staff, snapshot, 1)
        assert len(queries) == 1
        assert shown["state"] == "shown"
        family, head = shown["owners"]
        assert family == {
            "label": "Family record",
            "emails": ["family@example.org"],
            "phones": [],
        }
        assert head["label"] == "Member Example"
        assert head["emails"] == ["valid@example.org"]
        assert head["phones"] == [("Home", "202-555-0123")]
        # Family 2 has no heads and no contact record.
        assert family_contacts(staff, snapshot, 2)["owners"] == [
            {"label": "Family record", "emails": [], "phones": []}
        ]
        assert family_contacts(staff, snapshot, 999) == {"state": "missing"}
        # A random id stands in for a compacted or replaced snapshot: the
        # database refuses to compact the current, protected snapshot this
        # fixture holds, and the read has one test, "promoted and not
        # compacted", that an unknown id fails exactly as a compacted one.
        assert family_contacts(staff, uuid4(), 1) == {"state": "unavailable"}
        for roles in (frozenset({"ministry_leader"}), frozenset()):
            reader = Principal(uuid4(), roles, frozenset({4}))
            with (
                CaptureQueriesContext(connection) as queries,
                pytest.raises(PermissionError),
            ):
                family_contacts(reader, snapshot, 1)
            assert len(queries) == 0


@pytest.fixture
def crowded_service(monkeypatch, request):
    """The live service, its Family 1 also holding a child and an inactive head.

    Both extra Members have email and phone records of their own, so the
    only thing keeping them off the page is the head-only read.
    """
    original = response_builders.response_source

    def crowded():
        """The shared source plus a child and an inactive head of Family 1."""
        data = original()
        for duid, kind, status, name in (
            (5, "Child", "Active", "Child"),
            (6, "Head", "Inactive", "Retired"),
        ):
            data.members[duid] = {
                **data.members[3],
                "memberDUID": duid,
                "firstName": name,
                "memberType": kind,
                "memberStatus": status,
                "emailAddress": f"{name.lower()}@example.org",
                "homePhone": "202-555-0199",
            }
        return data

    monkeypatch.setattr(response_builders, "response_source", crowded)
    return request.getfixturevalue("live_response_service")


def test_other_members_are_left_out(crowded_service):
    """Children and inactive heads never appear, though their contacts exist."""
    snapshot = crowded_service.snapshot.pk
    staff = Principal(uuid4(), frozenset({"staff"}))
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        shown = family_contacts(staff, snapshot, 1)
    assert [owner["label"] for owner in shown["owners"]] == [
        "Family record",
        "Member Example",
    ]
    text = str(shown)
    assert "child@example.org" not in text and "retired@example.org" not in text
    assert "202-555-0199" not in text
