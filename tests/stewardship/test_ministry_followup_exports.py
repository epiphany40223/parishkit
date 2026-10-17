"""Member contact details on follow-up requests, and Export these requests (#1157).

The Members joining and Members leaving lists were retired: each follow-up
request's page shows its Member's contact details, and the follow-up queue's
Export these requests downloads its filtered set with them. These checks need
no database; the PostgreSQL tests cover scope and the real SQL.
"""

import csv
import io
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from parishkit.stewardship.audit.schemas import (
    Action,
    ContextKind,
    Outcome,
    sanitize,
)
from parishkit.stewardship.reports import ministry_contacts, ministry_followup_exports
from parishkit.stewardship.reports.information_rendering import render_information
from parishkit.stewardship.reports.ministry_followup import SORTING, FollowupQuery
from parishkit.stewardship.reports.ministry_followup_exports import (
    HEADINGS,
    _choices,
    queue_document,
)
from parishkit.stewardship.web.tables import report_table

SUBMITTED = datetime(2054, 10, 5, 14, 30, tzinfo=UTC)
CONTACTED = datetime(2054, 10, 7, 16, 0, tzinfo=UTC)
CHICAGO = ZoneInfo("America/Chicago")
JOIN, LEAVE, BARE, PROPOSED = (str(uuid4()) for _ in range(4))


def row(request_id, **values):
    """One queue row as ``followup_page`` shapes it."""
    return {
        "id": request_id,
        "member_name": "Example, Pat",
        "member_duid": 12345,
        "ministry_name": "Choir",
        "ministry_duid": 9,
        "action": "join",
        "cannot_serve": False,
        "state": "new",
        "state_label": "New",
        "outcome_label": "",
        "submitted_at": SUBMITTED,
        "last_contact_at": None,
    } | values


def join_details(**values):
    """A join request's details as ``request_contacts`` returns them."""
    return {
        "kind": "join",
        "ministry_duid": 9,
        "proposed": False,
        "proposed_id": None,
        "member_duid": 12345,
        "email_visibility": "available",
        "emails": ["pat@example.org"],
        "phone_visibility": "not_published",
        "phones": {},
        "address_lines": ["1 Example Street", "Exampleton, IN 46000"],
        "gender": "Female",
        "age": 41,
        "report_date": "2054-10-05",
    } | values


def leave_details(**values):
    """A leave request's details: role, plus the packet's email and phones."""
    return {
        "kind": "leave",
        "ministry_duid": 9,
        "proposed": False,
        "proposed_id": None,
        "member_duid": 23456,
        "email_visibility": "not_published",
        "emails": [],
        "phone_visibility": "available",
        "phones": {"mobile": "3175550100"},
        "current_role": "Member",
        "report_date": "2054-10-05",
    } | values


def result(rows):
    """The queue's whole selection for ``rows``."""
    return {
        "rows": rows,
        "total": len(rows),
        "metadata": {"name": "Renewal", "source_as_of": SUBMITTED},
    }


def document():
    """A queue export with a join, a leave, a request without details and a
    Member the Family added on the form."""
    rows = [
        row(JOIN, last_contact_at=CONTACTED),
        row(
            LEAVE,
            member_name="Sample, Lee",
            member_duid=23456,
            action="leave",
            cannot_serve=True,
            state="resolved",
            state_label="Resolved",
            outcome_label="Stayed in the Ministry",
        ),
        row(BARE, member_name="Gone, Old", member_duid=34567),
        row(PROPOSED, member_name="New, Kid", member_duid=None),
    ]
    contacts = {
        JOIN: join_details(),
        LEAVE: leave_details(),
        PROPOSED: join_details(proposed=True, proposed_id="p-1", member_duid=None),
    }
    return queue_document(
        result(rows),
        contacts,
        FollowupQuery(search="private"),
        parish_name="Example Parish",
        requested_at=SUBMITTED,
        zone=CHICAGO,
    )


def test_the_file_holds_the_queue_columns_then_the_contact_details():
    """Queue columns, Status split from Outcome, then contacts; no Submitted.

    The Administrator asked for the file without the Submitted time
    (2026-10-13), as the queue has no such column; the request page still
    shows it. Every format carries exactly these headings
    (``test_export_columns``).
    """
    built = document()
    assert built.headings == HEADINGS
    assert HEADINGS[:7] == (
        "Member",
        "Member DUID",
        "Ministry",
        "Request",
        "Status",
        "Outcome",
        "Last contact",
    )
    assert HEADINGS[-1] == "Current Ministry role"
    assert "Submitted" not in HEADINGS
    joined, left, bare, proposed = (
        dict(zip(HEADINGS, line, strict=True)) for line in built.rows
    )
    assert joined["Email"] == "pat@example.org"
    # A leader's view of an unpublished value never shows it.
    assert joined["Phones"] == "Not published"
    assert joined["Known primary Family address"] == (
        "1 Example Street\nExampleton, IN 46000"
    )
    assert (joined["Gender"], joined["Age"]) == ("Female", "41")
    assert joined["Current Ministry role"] == ""
    assert joined["Last contact"] == CONTACTED.astimezone(CHICAGO)
    # A leave has its role and the packet's email and phones, no join details.
    assert left["Request"] == "Leave (cannot participate)"
    assert (left["Status"], left["Outcome"]) == ("Resolved", "Stayed in the Ministry")
    assert left["Email"] == "Not published"
    assert left["Phones"].startswith("mobile: ")
    assert left["Current Ministry role"] == "Member"
    assert left["Gender"] == left["Age"] == left["Known primary Family address"] == ""
    assert left["Last contact"] == "None recorded"
    # Details the report could not read say so rather than leave a gap,
    # only in the cells a join has.
    assert bare["Email"] == bare["Phones"] == bare["Age"] == "Unavailable"
    assert bare["Current Ministry role"] == ""
    assert proposed["Member DUID"] == "New Member"


def test_the_file_names_its_scope_and_privacy_but_no_internal_reference():
    """Report details carry the filters and privacy line, never ids."""
    built = document()
    labels = dict(built.metadata)
    assert labels["Report"] == "Ministry follow-up requests"
    assert labels["Matching results"] == "4"
    assert labels["Display timezone"] == "America/Chicago"
    assert "Sensitive parish information" in labels["Privacy"]
    assert '"search": "private"' in labels["Filters and sort"]
    text = str(built.rows)
    assert all(request_id not in text for request_id in (JOIN, LEAVE, PROPOSED))
    assert "p-1" not in text


def test_an_unreadable_leave_leaves_the_join_cells_blank():
    """Only the cells a leave has say Unavailable."""
    built = queue_document(
        result([row(BARE, action="leave")]),
        {},
        FollowupQuery(),
        parish_name="P",
        requested_at=SUBMITTED,
        zone=CHICAGO,
    )
    (line,) = (dict(zip(HEADINGS, line, strict=True)) for line in built.rows)
    assert line["Email"] == line["Phones"] == "Unavailable"
    assert line["Current Ministry role"] == "Unavailable"
    assert line["Gender"] == line["Age"] == line["Known primary Family address"] == ""


def test_request_history_notes_that_values_are_current():
    """With replaced and withdrawn requests, the file says what the rows mean."""

    def labels(history):
        """The report details of a file for this history choice."""
        return dict(
            queue_document(
                result([]),
                {},
                FollowupQuery(history=history),
                parish_name="P",
                requested_at=SUBMITTED,
                zone=CHICAGO,
            ).metadata
        )

    assert "Request history" not in labels("current")
    note = labels("all")["Request history"]
    assert "not from when each version was submitted" in note


def test_two_new_members_with_one_name_are_numbered():
    """Rows stay distinct in every format, as the retired lists' did."""
    rows = [
        row(JOIN, member_name="New, Kid", member_duid=None),
        row(PROPOSED, member_name="New, Kid", member_duid=None),
    ]
    contacts = {
        JOIN: join_details(proposed=True, proposed_id="p-2", member_duid=None),
        PROPOSED: join_details(proposed=True, proposed_id="p-1", member_duid=None),
    }
    built = queue_document(
        result(rows),
        contacts,
        FollowupQuery(),
        parish_name="P",
        requested_at=SUBMITTED,
        zone=CHICAGO,
    )
    assert [line[1] for line in built.rows] == ["New Member 2", "New Member 1"]


def test_the_csv_is_exactly_the_table():
    """One header row, then one row a request, as every export's CSV."""
    output = io.BytesIO()
    render_information(document(), output, format="csv")
    lines = list(csv.reader(io.StringIO(output.getvalue().decode())))
    assert lines[0] == list(HEADINGS) and len(lines) == 5
    # No cell holds a request's submission time (2026-10-13).
    assert not any("2054-10-05" in cell for line in lines[1:] for cell in line)


@pytest.mark.parametrize(
    "values",
    [
        {"format": "doc", "timezone": "UTC"},
        {"format": "csv", "timezone": "Mars/Base"},
        {"format": "csv", "timezone": "UTC", "assignee": "any"},
        {"format": "csv", "timezone": "UTC", "state": "lost"},
        # The Staff-only Status choice for Members no longer in ParishSoft
        # (#1217) is never exported (Administrator, 2026-10-13).
        {"format": "csv", "timezone": "UTC", "state": "not_in_parishsoft"},
        {"timezone": "UTC"},
    ],
)
def test_the_form_grammar_is_closed(values):
    """Only the queue's own filters, a format and a zone are accepted."""
    with pytest.raises(ValueError):
        _choices(RequestFactory().post("/", values).POST)


def test_the_form_carries_the_queue_view():
    """The applied filters, search and sort make the file; page does not."""
    values = FollowupQuery(search="Pat", action="leave", state="any").form_values()
    post = (
        RequestFactory()
        .post("/", values | {"format": "xlsx", "timezone": "UTC", "page": "3"})
        .POST
    )
    query, fmt, zone = _choices(post)
    assert (query.search, query.action, query.state, query.page) == (
        "Pat",
        "leave",
        "any",
        1,
    )
    assert (fmt, zone.key) == ("xlsx", "UTC")


def test_a_query_string_is_refused(monkeypatch):
    """The export takes the queue's POSTed form only."""
    monkeypatch.setattr(ministry_followup_exports, "runtime", Mock())
    monkeypatch.setattr(ministry_followup_exports, "_principal", Mock())
    values = {"format": "csv", "timezone": "UTC"}
    request = RequestFactory().post("/?search=x", values)
    assert ministry_followup_exports.export(request, uuid4()).status_code == 400


def test_the_audit_keeps_scope_without_values():
    """Count, Ministry DUIDs and the contact projection pass the audit schema."""
    context = {
        "outcome": Outcome.SUCCEEDED,
        "count": 3,
        "ministry_duids": [9, 11],
        "ministry_operational": False,
    }
    safe = sanitize(ContextKind.ACTION, context)
    assert safe["ministry_duids"] == [9, 11] and safe["count"] == 3
    assert safe["ministry_operational"] is False
    assert Action.MINISTRY_FOLLOWUP_EXPORTED.value == "ministry_followup_exported"


def test_details_merge_the_report_and_the_packet(monkeypatch):
    """A join's details come from the report; a leave's email and phones
    from the packet, by request or, for an earlier version, by Member. A
    Ministry the report cannot read gives no details rather than failing."""
    earlier = str(uuid4())

    def page(campaign_id, query, principal, *, ministry_id, action, every):
        """The report's rows for one Ministry and kind."""
        assert every and query.history == "all" and query.state == "any"
        if ministry_id == 13:
            raise PermissionError("not in the report")
        base = {
            "entity_kind": "member",
            "proposed_id": None,
            "emails": [],
            "phones": {},
            "email_visibility": None,
            "phone_visibility": None,
        }
        rows = {
            "join": [
                base
                | {
                    "id": JOIN,
                    "member_duid": 1,
                    "gender": "Male",
                    "age": 30,
                    "address_lines": ["1 Street"],
                    "emails": ["a@example.org"],
                    "email_visibility": "available",
                    "phone_visibility": "not_published",
                }
            ],
            "leave": [
                base | {"id": LEAVE, "member_duid": 2, "current_role": "Chair"},
                base | {"id": earlier, "member_duid": 2, "current_role": "Chair"},
            ],
        }[action]
        return {"rows": rows, "metadata": {"report_date": "2054-10-05"}}

    packet = {"emails": ["b@example.org"], "phones": {}}
    monkeypatch.setattr(ministry_contacts, "ministry_page", page)
    monkeypatch.setattr(
        ministry_contacts,
        "packet_contacts",
        lambda campaign_id, principal, ministries: (
            {LEAVE: packet | {"email_visibility": "available"}},
            {(9, ("member", 2)): packet | {"email_visibility": "available"}},
        ),
    )
    requests = [
        {"id": JOIN, "ministry_duid": 9, "action": "join"},
        {"id": LEAVE, "ministry_duid": 9, "action": "leave"},
        {"id": earlier, "ministry_duid": 9, "action": "leave"},
        {"id": BARE, "ministry_duid": 13, "action": "join"},
    ]
    found = ministry_contacts.request_contacts(uuid4(), Mock(), requests)
    assert set(found) == {JOIN, LEAVE, earlier}
    assert found[JOIN]["emails"] == ["a@example.org"]
    assert found[JOIN]["phone_visibility"] == "not_published"
    assert found[LEAVE]["current_role"] == "Chair"
    assert found[LEAVE]["emails"] == found[earlier]["emails"] == ["b@example.org"]


def followup_page(**context):
    """The follow-up template rendered as its view does, from detached values."""
    query = FollowupQuery()
    return render_to_string(
        "stewardship/ministry-followup.html",
        {
            "campaign_id": uuid4(),
            "metadata": {"name": "Renewal", "source_as_of": SUBMITTED},
            "query": query,
            "rows": [],
            "total": 0,
            "ministries": [],
            "states": {},
            "outcomes": {},
            "mutable": True,
            "form": {},
            "history": [],
            "export_fields": query.form_values(),
            "export_timezones": ["America/Chicago", "UTC"],
            "export_pdf_limit": 1000,
            # Ministry requests' totals and packet (#1133), with no request.
            "totals": {"joining": 0, "leaving": 0, "requests": 0},
            "packet_limit": 200,
            "table": report_table(
                [],
                number=1,
                size=50,
                total=0,
                carry=[],
                sorting=SORTING,
                sort=query.sort,
                action="/queue",
            ),
        }
        | context,
    )


def item(**values):
    """A request page's request."""
    return {
        "id": JOIN,
        "member_name": "Example, Pat",
        "ministry_name": "Choir",
        "action": "join",
        "submitted_at": SUBMITTED,
        "state_label": "New",
        "outcome_label": "",
        "open": False,
        "latest": True,
    } | values


def contact_section(html):
    """The request page's Contact details section."""
    start = html.index('<section id="followup-contact"')
    return html[start : html.index("</section>", start)]


def test_the_queue_offers_export_these_requests():
    """The section carries the applied view, the source note and the zone."""
    html = followup_page(total=1)
    start = html.index('<section aria-labelledby="followup-export-heading">')
    section = html[start:]
    assert "Export these requests" in section
    assert 'id="table-export" data-table-sync' in section
    assert '<input type="hidden" name="search" value="">' in section
    assert '<input type="hidden" name="state" value="unresolved">' in section
    # No sensitive-data banner (#1539); the source note stays as help.
    assert "Sensitive parish" not in section
    assert "trusted device" not in section
    assert "Ministry leaders receive only published email and phone" in section
    assert 'name="timezone" data-browser-timezone' in section
    assert 'id="followup-export-count" data-table-sync' in section


def test_a_join_request_page_shows_its_members_contact_details():
    """Email, phones, address, gender and age, with Not published kept."""
    section = contact_section(followup_page(item=item(), contact=join_details()))
    assert "pat@example.org" in section
    assert "<dt>Phones</dt><dd>Not published</dd>" in section
    assert "1 Example Street<br>Exampleton, IN 46000" in section
    assert "<dt>Gender</dt><dd>Female</dd>" in section
    assert "Current Ministry role" not in section
    assert "added this Member on the form" not in section


def test_a_leave_request_page_shows_the_role_and_the_published_contacts():
    """A leave shows the Member's current role, email and phones."""
    html = followup_page(item=item(action="leave"), contact=leave_details())
    section = contact_section(html)
    assert "<dt>Current Ministry role</dt><dd>Member</dd>" in section
    assert "<dt>Email</dt><dd>Not published</dd>" in section
    assert "mobile: " in section
    assert "Gender" not in section and "Known primary Family address" not in section


def test_a_member_added_on_the_form_says_so():
    """Their details are what the Family entered."""
    contact = join_details(proposed=True, proposed_id="p-1", member_duid=None)
    section = contact_section(followup_page(item=item(), contact=contact))
    assert "added this Member on the form" in section


def test_no_details_say_so():
    """A request whose details cannot be read says so plainly."""
    section = contact_section(followup_page(item=item(), contact=None))
    assert "No contact details are available for this request." in section


def format_menu(html):
    """The export's format menu."""
    start = html.index('id="followup-export-format"')
    return html[start : html.index("</select>", start)]


def test_the_pdf_is_offered_only_up_to_its_limit():
    """Above the limit PDF is unavailable, and the export line says why;
    CSV and XLSX stay offered. The menu is redrawn with each applied view."""
    html = followup_page(total=3, export_pdf_limit=2)
    assert '<option value="pdf" disabled>PDF</option>' in format_menu(html)
    assert 'id="followup-export-format-field" data-table-sync' in html
    assert "The PDF is limited to 2 requests" in html
    html = followup_page(total=2, export_pdf_limit=2)
    assert '<option value="pdf">PDF</option>' in format_menu(html)
    assert "The PDF is limited" not in html


def test_a_pdf_above_the_limit_is_refused_before_contacts_are_read(monkeypatch):
    """A stale page or hand-made form asking for too large a PDF gets the
    typed refusal, and no contact detail is read for it; CSV is not limited."""
    rows = [row(str(uuid4())) for _ in range(3)]
    reads = []
    patch = monkeypatch.setattr
    patch(ministry_followup_exports, "EXPORT_PDF_LIMIT", 2)
    patch(ministry_followup_exports, "runtime", Mock())
    patch(ministry_followup_exports, "_principal", Mock())
    patch(ministry_followup_exports, "admit_report_read", Mock())
    patch(ministry_followup_exports, "_audit", Mock())
    patch(ministry_followup_exports, "followup_page", lambda *a, **k: result(rows))
    patch(
        ministry_followup_exports,
        "request_contacts",
        lambda *args: reads.append(args) or {},
    )
    patch(
        ministry_followup_exports,
        "campaign_response",
        lambda request, campaigns, **options: (options["open_content"](), Mock())[1],
    )
    values = FollowupQuery().form_values() | {"format": "pdf", "timezone": "UTC"}
    response = ministry_followup_exports.export(
        RequestFactory().post("/", values), uuid4()
    )
    assert response.status_code == 400 and not reads
    assert str(response.stewardship_refusal.message) == (
        "The PDF is limited to 2 requests."
    )
    assert "CSV or XLSX" in str(response.stewardship_refusal.fix)


def test_the_file_reads_the_queues_own_selection(monkeypatch):
    """The rows are the queue's whole selection with its default presence.

    Members no longer in ParishSoft must stay out of the file (Administrator,
    2026-10-13). The queue's selection leaves them out by default (#1217), so
    the export must read it exactly as the queue does: never with
    ``every_member``, which Roster changes to enter alone uses to list them.
    """
    calls = []

    def selection(campaign_id, query, principal, **options):
        """Record the read, then end the export (nothing else is under test)."""
        calls.append((query, options))
        raise PermissionError("stop here")

    patch = monkeypatch.setattr
    patch(ministry_followup_exports, "runtime", Mock())
    patch(ministry_followup_exports, "_principal", Mock())
    patch(ministry_followup_exports, "admit_report_read", Mock())
    patch(ministry_followup_exports, "_audit", Mock())
    patch(ministry_followup_exports, "followup_page", selection)
    patch(
        ministry_followup_exports,
        "campaign_response",
        lambda request, campaigns, **options: (options["open_content"](), Mock())[1],
    )
    values = FollowupQuery(action="join").form_values() | {
        "format": "csv",
        "timezone": "UTC",
    }
    ministry_followup_exports.export(RequestFactory().post("/", values), uuid4())
    assert calls == [(FollowupQuery(action="join"), {"whole": True})]
