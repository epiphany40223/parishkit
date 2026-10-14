"""Ministry and Member tables give each DUID its own column (#932).

The Ministry report's summary and the Campaign Ministries review follow
the admin-portal spec's "Table column order": dates first, then the Member
or Ministry name, then its DUID in a column of its own.
"""

from datetime import UTC, datetime

from django.template.loader import render_to_string

from parishkit.stewardship.reports.chart_specs import ministry_followup
from parishkit.stewardship.reports.ministries import (
    SUMMARY_SORTING,
    TOTALS,
    MinistryQuery,
    summary_totals,
)
from parishkit.stewardship.web.tables import report_table

SUBMITTED = datetime(2054, 10, 5, 14, 30, tzinfo=UTC)


def ministry_page(rows, *, summaries=(), packet_choices=()):
    """Render the Ministry report as its view does, from detached rows.

    The page is the campaign-wide summary only; ``rows`` is kept so callers
    read alike, but one Ministry's request lists were retired (#1157).
    """
    query = MinistryQuery()
    shown = list(summaries)
    # The page's totals and follow-up chart (#720), as the view builds them;
    # a sample row without a count adds nothing.
    totals = summary_totals([{key: row.get(key, 0) for key in TOTALS} for row in shown])
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
            "totals": totals,
            "followup_chart": ministry_followup(totals),
            "packet_choices": list(packet_choices),
            "packet_limit": 200,
            "rows": rows,
            "total": len(shown),
            "mutable": True,
            "query": query,
            # As Staff or an Administrator sees it: with the ParishSoft and
            # campaign marks.
            "show_marks": True,
            "report_url": "/report",
            "table": report_table(
                shown,
                number=1,
                size=50,
                total=len(shown),
                carry=[],
                sorting=SUMMARY_SORTING,
                sort=query.sort,
                action="/report",
            ),
        },
    )


def in_order(html, *texts):
    """Whether every one of ``texts`` appears in ``html``, in this order."""
    positions = [html.index(text) for text in texts]
    return positions == sorted(positions)


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
    # A Ministry ParishSoft lists has no mark: there is no Active or
    # Inactive any more (#1267).
    assert '<th scope="row">Food pantry</th>' in table
    assert "Active" not in table and "Inactive" not in table
    assert "DUID" not in table and 'class="numeric">9<' not in table


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
