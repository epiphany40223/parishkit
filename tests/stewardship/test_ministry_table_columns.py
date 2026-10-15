"""Ministry and Member tables give each DUID its own column (#932).

Ministry requests' By Ministry counts and the Campaign Ministries review
follow the admin-portal spec's "Table column order": the Member or Ministry
name, and no Ministry DUID (#1123).
"""

from datetime import UTC, datetime
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.chart_specs import ministry_followup
from parishkit.stewardship.reports.ministry_followup import (
    FILTER_OUTCOMES,
    SORTING,
    STATES,
    FollowupQuery,
)
from parishkit.stewardship.reports.ministry_followup_views import (
    BULK_KIND_HINTS,
    bulk_actions,
)
from parishkit.stewardship.web.tables import report_table

SUBMITTED = datetime(2054, 10, 5, 14, 30, tzinfo=UTC)


def ministry_page(rows=(), *, summaries=(), packet_choices=(), totals=None, **extra):
    """Render Ministry requests (#1133) as its queue view does, from detached
    values: the queue's ``rows``, the By Ministry ``summaries`` and the
    packet's choices. ``totals`` default to none of either request, so the
    follow-up chart's bars read zero; ``extra`` overrides any other context value.
    """
    query = FollowupQuery()
    rows = list(rows)
    totals = totals or {
        "joining": 0,
        "leaving": 0,
        "requests": 0,
        "unresolved": 0,
        "completed": 0,
    }
    context = {
        "metadata": {"name": "Renewal", "source_as_of": SUBMITTED},
        "rows": rows,
        "total": len(rows),
        "ministries": [],
        "query": query,
        "table": report_table(
            rows,
            number=1,
            size=50,
            total=len(rows),
            carry=[],
            sorting=SORTING,
            sort=query.sort,
            action="/admin/reports/ministries/",
        ),
        "mutable": True,
        "item": None,
        "states": STATES,
        "outcomes": FILTER_OUTCOMES,
        "bulk_actions": bulk_actions(),
        "bulk_kind_hints": BULK_KIND_HINTS,
        "bulk_key": UUID(int=1),
        "export_fields": query.form_values(),
        "export_timezones": ["UTC"],
        "export_pdf_limit": 1000,
        "totals": totals,
        "followup_chart": ministry_followup(totals),
        "by_ministry": list(summaries),
        # As Staff or an Administrator sees it: with the ParishSoft and
        # campaign marks (#1135).
        "show_marks": True,
        "campaign_marks": True,
        "packet_choices": list(packet_choices),
        "packet_limit": 200,
        "packet_key": UUID(int=2),
    }
    return render_to_string("stewardship/ministry-followup.html", context | extra)


def in_order(html, *texts):
    """Whether every one of ``texts`` appears in ``html``, in this order."""
    positions = [html.index(text) for text in texts]
    return positions == sorted(positions)


def test_by_ministry_names_each_ministry_without_its_duid():
    """By Ministry's name heads each row; no Ministry DUID (#1123, #1133)."""
    summary = {
        "name": "Food pantry",
        "duid": 9,
        "active": True,
        "in_campaign": True,
        "joining": 1,
        "leaving": 0,
        "unresolved": 1,
    }
    other = summary | {"name": "Choir", "duid": 8}
    html = ministry_page(summaries=[summary, other])
    start = html.index('id="by-ministry"')
    table = html[start : html.index("</table>", start)]
    assert in_order(table, ">Ministry</th>", ">Join requests</th>", ">Unresolved</th>")
    # A Ministry ParishSoft lists has no mark: there is no Active or
    # Inactive any more (#1267).
    assert '<th scope="row">Food pantry</th>' in table
    assert "Active" not in table and "Inactive" not in table
    assert "DUID" not in table and ">9<" not in table


def test_campaign_ministries_review_names_ministries_without_duids():
    """Ministries to add and remove are named, never shown by DUID (#1123)."""
    html = render_to_string(
        "stewardship/campaign-ministries-review.html",
        {
            "ministries": {
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
