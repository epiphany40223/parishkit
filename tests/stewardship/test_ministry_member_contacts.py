"""A Member's email and phones while reviewing Ministry requests (#2398).

Ministry requests' Member cell lists the requesting Member's email and
phones under the name and its DUID, for every role, read for the shown rows
in one read of the packet projection, which applies the request page's
contact rule; the Family hub's Member tabs read none. The PostgreSQL tests
(database/test_ministry_contacts_postgresql.py) check the rule itself.
"""

import re
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.reports import ministry_contacts, ministry_worklist
from parishkit.stewardship.reports.ministry_contacts import (
    row_contacts,
)
from parishkit.stewardship.reports.ministry_worklist import (
    WorklistQuery,
    contact_ministries,
    place,
    worklist_context,
)

from .test_roster import LEADER, STAFF, request_row

ADMIN = STAFF.__class__(UUID(int=96), frozenset({"administrator"}))
JOIN, LEAVE, OLD, ADDED, ELSEWHERE = (str(UUID(int=n)) for n in range(1, 6))
SHARED = {
    "email_visibility": "available",
    "emails": ["pat.sample@example.org"],
    "phone_visibility": "available",
    "phones": {"home": "+15025550100", "mobile": "+15025550123", "work": ""},
}
WITHHELD = {
    "email_visibility": "not_published",
    "emails": [],
    "phone_visibility": "available",
    "phones": {"home": "+15025550100"},
}
ENTERED = SHARED | {"emails": ["new@example.org"], "phones": {"mobile": "5025551212"}}


def packet(monkeypatch, *, by_id=None, by_member=None):
    """Stub the packet read; returns the list of its calls' Ministries."""
    calls = []

    def read(campaign_id, principal, ministries):
        """The packet's two maps, noting each read's Ministries."""
        calls.append(ministries)
        return by_id or {}, by_member or {}

    monkeypatch.setattr(ministry_contacts, "packet_contacts", read)
    return calls


def test_row_contacts_reads_the_packet_once_for_the_rows_shown(monkeypatch):
    """One read for every current Ministry of the rows (an Edit's, else the
    Family's); a row takes its own request's values, an earlier version its
    Member's under the same Ministry, a Member the Family added theirs by
    form reference, and a row the read has nothing for is left out."""
    calls = packet(
        monkeypatch,
        by_id={JOIN: SHARED, LEAVE: WITHHELD},
        by_member={
            (9, ("member", 3)): SHARED,
            (4, ("proposed", "p-1")): ENTERED,
        },
    )
    rows = [
        request_row(id=JOIN),
        request_row(id=LEAVE, ministry_duid=4, reviewed_ministry_duid=9),
        request_row(id=OLD, latest=False),
        request_row(id=ADDED, member_duid=None, proposed_id="p-1", ministry_duid=4),
        request_row(id=ELSEWHERE, member_duid=8, ministry_duid=4),
    ]
    found = row_contacts(UUID(int=92), STAFF, rows)
    assert calls == [{9, 4}]
    assert found == {JOIN: SHARED, LEAVE: WITHHELD, OLD: SHARED, ADDED: ENTERED}
    assert row_contacts(UUID(int=92), STAFF, []) == {}
    assert calls == [{9, 4}]


def worklist(monkeypatch, principal, rows, *, contacts):
    """Ministry requests' worklist context for detached selection ``rows``,
    reading contacts through a stubbed ``row_contacts``; returns the context
    and the rows each contacts read was given."""
    reads = []

    def read(campaign_id, reader, shown):
        """Each row's contacts, from ``by_id``, noting the rows read."""
        reads.append([row["id"] for row in shown])
        return {row["id"]: by_id[row["id"]] for row in shown if row["id"] in by_id}

    by_id = {JOIN: SHARED, LEAVE: WITHHELD}
    monkeypatch.setattr(ministry_worklist, "row_contacts", read)
    result = {
        "rows": rows,
        "ministries": [dict(duid=9, name="Food pantry", in_campaign=True)],
        "metadata": {},
    }
    place(result, principal, marks={})
    context = worklist_context(
        UUID(int=92),
        WorklistQuery(stage="all"),
        principal,
        result,
        token="",
        mutable=True,
        action=reverse("admin:ministry_report"),
        decisions={},
        asked={},
        families={JOIN: UUID(int=81), LEAVE: UUID(int=81)},
        contacts=contacts,
    )
    return context, reads


def member_cells(context, principal):
    """Each row's Member cell (its row header) as rendered for ``principal``."""
    html = render_to_string(
        "stewardship/components/review-worklist.html",
        context | {"csrf_token": "x", "request": SimpleNamespace(principal=principal)},
    )
    return re.findall(r'<th scope="row">(.*?)</th>', html, flags=re.S)


def test_every_role_sees_the_members_contact_lines_under_the_name(monkeypatch):
    """Leader, Staff and Administrator: the email as a mail link and each
    phone, labelled, as a call link, after the name and its DUID in the
    same cell (no new column), outside the name's Family hub link and its
    clamp; Not published where the rule withholds it; one contacts read for
    the shown rows."""
    rows = [
        request_row(id=JOIN, state="new", outcome=None),
        request_row(id=LEAVE, action="leave", state="new", outcome=None),
    ]
    for principal in (LEADER, STAFF, ADMIN):
        context, reads = worklist(monkeypatch, principal, rows, contacts=True)
        assert reads == [[JOIN, LEAVE]]
        headings = [column.heading for column in context["worklist"].shown_columns]
        assert headings == ["Member", "Ministry"]
        shared, withheld = member_cells(context, principal)
        name, rest = shared.split('<span class="stack-line cell-detail nowrap">')
        # The name (a hub link for Staff, plain for a leader) is clamped
        # alone; the contact lines follow the DUID.
        assert name.count("name-clamp") == 1 and "mailto:" not in name
        assert (principal is LEADER) == ("<a " not in name)
        duid, lines = rest.split('<span class="member-contact">')
        assert duid == '<span class="visually-hidden">Member DUID </span>3</span>'
        assert lines == (
            '<span class="contact-line contact-email"><span class="contact-kind">'
            'Email:</span> <a href="mailto:pat.sample@example.org">'
            "pat.sample@example.org</a></span>"
            '<span class="contact-line"><span class="contact-kind">Mobile:</span> '
            '<a href="tel:+15025550123"><span class="nowrap">+1 (502) 555-0123'
            "</span></a></span>"
            '<span class="contact-line"><span class="contact-kind">Home:</span> '
            '<a href="tel:+15025550100"><span class="nowrap">+1 (502) 555-0100'
            "</span></a></span>"
            "</span>"
        )
        assert '<span class="contact-line">Email: Not published</span>' in withheld
        assert "Home:" in withheld and "mailto:" not in withheld
        # The view's audit names the Ministry they were read under.
        assert contact_ministries(context["table"].rows) == [9]


def test_a_row_with_no_contacts_and_the_hub_draw_no_lines(monkeypatch):
    """A row the read has nothing for draws no contact line, and the Family
    hub's Member tabs (contacts None) read nothing at all."""
    rows = [request_row(id=OLD, state="new", outcome=None)]
    context, reads = worklist(monkeypatch, STAFF, rows, contacts=True)
    assert reads == [[OLD]]
    (cell,) = member_cells(context, STAFF)
    assert "member-contact" not in cell
    assert contact_ministries(context["table"].rows) == []
    context, reads = worklist(monkeypatch, STAFF, rows, contacts=None)
    assert reads == []
    assert all(row["contact"] is None for row in context["worklist"].rows)
