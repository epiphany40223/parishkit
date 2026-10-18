"""Ministry and Member tables give each DUID its own column (#932).

Ministry requests' By Ministry counts and the Campaign Ministries review
follow the admin-portal spec's "Table column order": the Member or Ministry
name, and no Ministry DUID (#1123).
"""

from datetime import UTC, datetime
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.pipeline import Stream
from parishkit.stewardship.reports.ministry_followup import SORTING
from parishkit.stewardship.reports.ministry_worklist import (
    COLUMNS,
    MEMBERS,
    PREFIX,
    WorklistQuery,
    stage_choices,
)
from parishkit.stewardship.web.review_worklist import build
from parishkit.stewardship.web.tables import report_table

SUBMITTED = datetime(2054, 10, 5, 14, 30, tzinfo=UTC)


def ministry_page(rows=(), *, summaries=(), packet_choices=(), roles=None, **extra):
    """Render Ministry requests (#1133) as its worklist view does (#1684),
    from detached values: the worklist's decorated ``rows`` (as
    ``ministry_worklist.decorate`` gives them), the By Ministry
    ``summaries`` and the packet's choices, for a Staff reader unless
    ``roles`` says otherwise; ``extra`` overrides any other context value.
    """
    query = WorklistQuery()
    rows = list(rows)
    roles = frozenset({"staff"}) if roles is None else roles
    table = report_table(
        rows,
        number=1,
        size=50,
        total=len(rows),
        carry=[],
        sorting=SORTING,
        sort=query.sort,
        action="/admin/reports/ministries/",
    )
    worklist = build(
        Stream.MINISTRY,
        table.rows,
        roles,
        prefix=PREFIX,
        action="/admin/reports/ministries/",
        columns=COLUMNS,
        stage=query.stage,
        counts={},
        stages=(),
        items="requests",
        caption="Ministry requests to review",
        baseline=False,
        fields={"page": "1"},
        filters_template="stewardship/ministry-worklist-filters.html",
        editor_template="stewardship/ministry-worklist-edit.html",
        add_template="stewardship/ministry-worklist-add.html",
        export_template="stewardship/ministry-worklist-export.html",
    )
    worklist.stage_choices = stage_choices({}, "staff" not in roles)
    context = {
        "metadata": {"name": "Renewal", "source_as_of": SUBMITTED},
        "worklist": worklist,
        "table": table,
        "total": len(rows),
        "ministries": [],
        "query": query,
        "mutable": True,
        "notice": None,
        "can_tick": "staff" in roles,
        "members_choices": MEMBERS if "staff" in roles else None,
        "add_ministries": [],
        "add_replaceable": [],
        "export_fields": query.form_values(),
        "export_timezones": ["UTC"],
        "export_pdf_limit": 1000,
        "by_ministry": list(summaries),
        # As Staff or an Administrator sees it: with the ParishSoft and
        # campaign marks (#1135).
        "show_marks": True,
        "campaign_marks": True,
        "packet_choices": list(packet_choices),
        "packet_limit": 200,
        "packet_key": UUID(int=2),
    }
    return render_to_string("stewardship/ministry-requests.html", context | extra)


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
