"""Ministry and Member tables give each DUID its own column (#932).

The Ministry report's summary and request details, and the Campaign
Ministries review, follow the admin-portal spec's "Table column order":
dates first, then the Member or Ministry name, then its DUID in a column of
its own.
"""

from datetime import UTC, datetime

from django.template.loader import render_to_string

from parishkit.stewardship.reports.ministries import (
    DETAIL_SORTING,
    SUMMARY_SORTING,
    MinistryQuery,
)
from parishkit.stewardship.web.tables import report_table

SUBMITTED = datetime(2054, 10, 5, 14, 30, tzinfo=UTC)


def ministry_page(rows, *, ministry_id=None, summaries=(), packet_choices=()):
    """Render the Ministry report as its view does, from detached rows."""
    query = MinistryQuery()
    shown = rows if ministry_id is not None else list(summaries)
    return render_to_string(
        "stewardship/ministry-report.html",
        {
            "metadata": {
                "name": "Renewal",
                "report_date": SUBMITTED.date(),
                "timezone": "America/Chicago",
                "source_as_of": SUBMITTED,
                "source_generation": 1,
            },
            "summaries": list(summaries),
            "packet_choices": list(packet_choices),
            "packet_limit": 200,
            "rows": rows,
            "total": len(shown),
            "mutable": True,
            "export_fields": {},
            "export_timezones": ["UTC"],
            "ministry_id": ministry_id,
            "action": "join" if ministry_id is not None else None,
            "query": query,
            "states": {},
            # As Staff or an Administrator sees it: with Ministry activity.
            "show_activity": True,
            "report_url": "/report",
            "summary_url": "/report",
            "table": report_table(
                shown,
                number=1,
                size=50,
                total=len(shown),
                carry=[],
                sorting=DETAIL_SORTING if ministry_id is not None else SUMMARY_SORTING,
                sort=query.sort,
                action="/report",
            ),
        },
    )


def request_row(**values):
    """One request-detail row as the selection returns it."""
    return {
        "member_name": "Pat Example",
        "member_duid": 12345,
        "proposed_id": None,
        "submitted_at": SUBMITTED,
        "state_label": "New",
        "outcome_label": "",
        "gender": None,
        "age": None,
        "email_visibility": "published",
        "emails": [],
        "phone_visibility": "published",
        "phones": {},
        "address_lines": [],
    } | values


def in_order(html, *texts):
    """Whether every one of ``texts`` appears in ``html``, in this order."""
    positions = [html.index(text) for text in texts]
    return positions == sorted(positions)


def test_request_details_lead_with_member_and_member_duid():
    """Member (row header), Member DUID, status, then Submitted (#932)."""
    proposed = request_row(
        member_name="New Person", member_duid=None, proposed_id="p-1"
    )
    html = ministry_page([request_row(), proposed], ministry_id=9)
    start = html.index("Authorized Member request details")
    table = html[start : html.index("</table>", start)]
    assert in_order(
        table,
        'data-sort-column="member"',
        # Both DUID columns sort through the selection (#960).
        'class="numeric" data-sort-column="member_duid"',
        ">Status and outcome</th>",
        'data-sort-column="submitted"',
    )
    assert '<th scope="row">Pat Example</th>\n<td class="numeric">12345</td>' in table
    # A Member added on the form has no DUID yet: the row header is just the
    # name, and the DUID cell says New Member, with the form note and the
    # technical reference beside it. That cell is text, so it is not numeric.
    start = table.index('<th scope="row">New Person</th>\n')
    cell = table[start : table.index("</td>", start)]
    assert in_order(
        cell,
        "<td>New Member<br>Added on the form (not yet in ParishSoft)",
        "Proposed Member ID",
        "<code>p-1</code>",
    )
    assert "DUID 12345" not in table


def test_summary_names_each_ministry_without_its_duid():
    """The summary's Ministry name heads the row; no Ministry DUID (#1123)."""
    summary = {
        "name": "Food pantry",
        "duid": 9,
        "active": True,
        "in_campaign": True,
        "joining": 1,
        "leaving": 0,
        "unresolved": 1,
        "progress": "0 of 1",
    }
    html = ministry_page([], summaries=[summary])
    start = html.index("Current Ministry request summary")
    table = html[start : html.index("</table>", start)]
    assert in_order(table, 'data-sort-column="ministry"', ">Joining</th>")
    assert '<th scope="row">Food pantry<br>Active</th>' in table
    assert "DUID" not in table and 'class="numeric">9<' not in table


def test_one_ministrys_export_line_names_the_ministry():
    """One Ministry's export line names it, never its DUID (#1123)."""
    summary = {
        "name": "Food pantry",
        "duid": 9,
        "active": True,
        "in_campaign": True,
        "joining": 1,
        "leaving": 0,
        "unresolved": 1,
        "progress": "0 of 1",
    }
    html = ministry_page([], summaries=[summary], ministry_id=9)
    count = html[html.index('id="ministry-export-count"') :]
    count = count[: count.index("</p>")]
    assert "Prospective joiners — Food pantry." in count
    assert "DUID" not in count


def test_one_ministrys_export_line_without_its_summary():
    """When the filters hide the Ministry's summary row, the line says so
    rather than printing an empty name."""
    html = ministry_page([], summaries=[], ministry_id=9)
    count = html[html.index('id="ministry-export-count"') :]
    count = count[: count.index("</p>")]
    assert "Prospective joiners — Unavailable for these filters." in count
    assert "— ." not in count


def test_campaign_ministries_review_names_ministries_without_duids():
    """Ministries to add and remove are named, never shown by DUID (#1123)."""
    html = render_to_string(
        "stewardship/campaign-ministries-review.html",
        {
            "review": {
                "added": [{"name": "Choir", "duid": 8, "answers": 0}],
                "removed": [
                    {"name": "Food pantry", "duid": 9, "answers": 1, "open": 1}
                ],
                "open_forms": 0,
            },
        },
    )
    assert in_order(html, ">Ministry</th>", ">Submitted answers")
    assert "<li>Choir</li>" in html
    row = '<th scope="row">Food pantry</th><td>1</td><td>1</td>'
    assert row in html
    assert "DUID" not in html


def test_packet_choices_are_labelled_by_full_name_only():
    """Each packet box names its Ministry in full, with no DUID (#1123)."""
    name = "Saint Vincent de Paul Society Home Visit and Food Delivery Team"
    html = ministry_page([], packet_choices=[{"duid": 9, "name": name}])
    choices = html[html.index('class="packet-choices"') :]
    label = choices[: choices.index("</label>")]
    assert label.endswith(f'value="9"> {name}')
    assert "DUID" not in label
