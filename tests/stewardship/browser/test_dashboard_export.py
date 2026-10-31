"""The Response dashboard's download carries every figure the page shows (#1557).

The page is the real template drawn from the component's synthetic values
(``response_dashboard_components``). Export XLSX posts the page's export
form; the test answers that post with the workbook the export worker writes
from the same values (the capture's ``summary_document`` and the daily
chart), downloads it as the browser does, and checks it against what the
page shows: every Summary tile with its value, share and note, and every
daily row.
"""

import io
import re
from dataclasses import replace
from datetime import datetime

import pytest
from django.urls import reverse
from openpyxl import load_workbook

from ..test_daily_digest_content import document
from .response_dashboard_components import dashboard_metrics

pytestmark = pytest.mark.parametrize("browser_engine", ["chromium"], indirect=True)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
POPULATION = '#response-summary [aria-label="Population"] .stat-tile'
STAGES = '#response-summary [aria-label="Families by stage"] .stat-tile'


def workbook():
    """The workbook the worker writes for the page's values, as bytes."""
    from parishkit.stewardship.reports.participation_summary import (
        ParticipationSummary,
        summary_document,
    )
    from parishkit.stewardship.reports.spreadsheets import participation_xlsx

    value = document()
    captured = summary_document(value.statistics, dashboard_metrics(), "historical")
    chart = replace(
        value.participation,
        browser_timezone="America/Los_Angeles",
        summary=ParticipationSummary.from_document(captured),
    )
    output = io.BytesIO()
    participation_xlsx(chart, output)
    return output.getvalue()


def amount(text):
    """A tile's figure as a number: "$1,234" or "1,234" reads 1234."""
    return float(re.sub(r"[$,]", "", text))


def tiles(page, selector):
    """Each tile's label, value text and full text, in page order."""
    return [
        (
            tile.locator(".stat-label").inner_text(),
            tile.locator(".stat-value").inner_text().split("\n")[0].strip(),
            tile.inner_text(),
        )
        for tile in page.locator(selector).all()
    ]


def test_the_downloaded_workbook_holds_every_figure_on_the_page(
    page, component_origin, tmp_path
):
    """Export XLSX downloads a workbook whose Summary sheet has each tile of
    both Summary rows, in page order, with the page's value, share and note,
    and whose daily sheet has every row of the daily table."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/response-dashboard")
    route = reverse("admin:report_export_create")
    posts = []

    def answer(found):
        """Keep the post, then answer it with the export's file."""
        posts.append(found.request.post_data)
        found.fulfill(
            status=200,
            body=workbook(),
            headers={
                "Content-Type": XLSX,
                "Content-Disposition": 'attachment; filename="participation.xlsx"',
            },
        )

    page.route("**" + route, answer)
    with page.expect_download() as info:
        page.get_by_role("button", name="Export XLSX").click()
    path = tmp_path / "participation.xlsx"
    info.value.save_as(path)
    [body] = posts
    assert "format=xlsx" in body and "fact_set_id=" in body
    book = load_workbook(path)
    try:
        assert book.sheetnames == [
            "Summary",
            "Daily participation",
            "Report information",
        ]
        rows = [
            [cell.value for cell in row] for row in book["Summary"].iter_rows(min_row=2)
        ]
        population = [row for row in rows if row[0] == "Population"]
        stages = [row for row in rows if row[0] == "Families by stage"]
        shown = tiles(page, POPULATION)
        assert [row[1] for row in population] == [
            *(label for label, _, _ in shown),
            "Data as of",
        ]
        for (_, text, _), (_, _, value, _, _) in zip(shown, population, strict=False):
            if text == "—":
                assert value == "Unavailable"
            else:
                assert amount(text) == value
        assert isinstance(population[-1][2], datetime)
        shown = tiles(page, STAGES)
        assert [row[1] for row in stages] == [label for label, _, _ in shown]
        for (_, text, full), (_, _, value, share, note) in zip(
            shown, stages, strict=True
        ):
            assert amount(text) == value
            if share is None:
                assert "compared with invited" not in full
            else:
                assert f"{round(share * 100)}% compared with invited" in full
            if note:
                assert note.rstrip(".") in full
        table = page.locator("[data-table-region] tbody tr")
        daily = list(book["Daily participation"].iter_rows(min_row=2))
        assert len(daily) == table.count()
    finally:
        book.close()
