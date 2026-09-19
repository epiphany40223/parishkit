"""Report query safety, shared display parity and typed spreadsheet outputs."""

from contextlib import ExitStack
from dataclasses import replace
from decimal import Decimal
from io import BytesIO
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.http import QueryDict
from openpyxl import load_workbook

from parishkit.stewardship.reports.daily_digest import (
    population_cards,
    statistics_cards,
)
from parishkit.stewardship.reports.digest_presentation import (
    participation_context,
    snapshot_context,
)
from parishkit.stewardship.reports.spreadsheets import participation_xlsx
from parishkit.stewardship.reports.workspace import RenderedReport, ReportQuery

from .test_daily_digest_content import document


@pytest.mark.parametrize(
    "system_open,known",
    [(False, False), (False, True), (True, False), (True, True)],
)
def test_read_admission_classifies_only_known_campaign_closure(
    monkeypatch, system_open, known
):
    """Global/unknown denials never turn into a lifecycle retry or acceptance."""
    from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
    from parishkit.stewardship.reports import read_admission

    system, campaign = Mock(), Mock()
    system.objects.filter.return_value.exists.return_value = system_open
    campaign.objects.filter.return_value.exists.return_value = known
    monkeypatch.setattr(read_admission, "SystemConfiguration", system)
    monkeypatch.setattr(read_admission, "Campaign", campaign)
    admission = Mock(side_effect=PermissionError("private refusal"))
    monkeypatch.setattr(read_admission, "admit_campaign", admission)
    identifier = uuid4()
    error = ReadUnavailable if system_open and known else PermissionError
    with pytest.raises(error, match="unavailable") as caught:
        read_admission.admit_report_read(identifier)
    assert "private refusal" not in str(caught.value)
    admission.assert_called_once_with(identifier, mutating=False)
    system.objects.filter.assert_called_once_with(
        active_configuration_id__isnull=False, restore_review_required=False
    )
    if system_open:
        campaign.objects.filter.assert_called_once_with(pk=identifier)
    else:
        campaign.objects.filter.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        "scope=all",
        "page=0",
        "page=01",
        "page=10001",
        "page=-1",
        "page=1&page=2",
        "page=",
        "page=１２",
        "sort=name",
        "email=private@example.org",
        "inactive=1",
        "timezone=invalid",
    ],
)
def test_queries_are_bounded_nonidentifying_and_closed(query):
    with pytest.raises(ValueError):
        ReportQuery.parse(QueryDict(query))


def test_campaign_links_preserve_only_validated_state():
    value = ReportQuery.parse(
        QueryDict(
            "scope=current&timezone=America%2FDetroit&inactive=yes&page=2&sort=date_desc"
        )
    )
    url = value.url(document().participation.campaign_id, page=3)
    assert "scope=current" in url and "page=3" in url
    assert "timezone=America%2FDetroit" in url and "sort=date_desc" in url
    assert "inactive=yes" in url
    assert "timezone=" not in ReportQuery.parse(QueryDict()).url(
        document().participation.campaign_id
    )
    assert "timezone=UTC" in ReportQuery.parse(QueryDict("timezone=UTC")).url(
        document().participation.campaign_id
    )


@pytest.mark.parametrize("consume", [False, True])
def test_rendered_report_releases_generation_even_before_first_byte(consume):
    closed = []
    stack = ExitStack()
    stack.callback(closed.append, True)
    response = RenderedReport(b"report", stack)
    if consume:
        assert next(response) == b"report"
    response.close()
    response.close()
    assert closed == [True]


def test_live_presentation_is_the_digest_presentation_without_population_mixing():
    value = document()
    live = participation_context(value.participation)
    retained = snapshot_context(
        value, mode="testing", chart_url="/chart", download_url="/download"
    )
    for field in ("rows", "headings", "chart_interaction", "chart_last_index"):
        assert live[field] == retained[field]
    assert all(
        "Historical as of day" in label for label in live["chart_interaction"]["labels"]
    )
    cards = statistics_cards(replace(value.statistics, active=None))
    assert all(amount == "Unavailable" for _, amount in cards)
    inactive = population_cards(
        value.statistics.active, financial_enabled=True, inactive=True
    )
    assert inactive[0] == ("Inactive Families", "1,000")


def test_xlsx_is_complete_typed_formula_safe_and_keeps_original_request():
    value = replace(
        document().participation, parish_name="=HYPERLINK(1)", campaign_name="+SUM(1)"
    )
    stream = BytesIO()
    participation_xlsx(value, stream)
    stream.seek(0)
    book = load_workbook(stream)
    sheet = book["Participation"]
    assert sheet.max_row == len(value.days) + 1
    assert sheet["C2"].data_type == "n" and sheet["C2"].value == 1
    assert sheet["I2"].data_type == "n" and sheet["I2"].value == 1234.56
    assert sheet["A2"].is_date
    assert sheet.freeze_panes == "A2" and sheet.auto_filter.ref == "A1:I4"
    assert sheet.print_title_rows == "$1:$1"
    metadata = book["Report information"]
    assert metadata["B1"].value == "=HYPERLINK(1)" and metadata["B1"].data_type == "s"
    assert value.as_of_label == metadata["B8"].value
    assert not any(
        cell.data_type == "f" for page in book for row in page for cell in row
    )
    book.close()


def test_large_money_stays_exact_instead_of_excel_rounding():
    value = document().participation
    day = replace(value.days[0], pledge_total=Decimal("1234567890123456.78"))
    stream = BytesIO()
    participation_xlsx(replace(value, days=(day, *value.days[1:])), stream)
    stream.seek(0)
    book = load_workbook(stream)
    assert book["Participation"]["I2"].value == "1234567890123456.78"
    assert book["Participation"]["I2"].data_type == "s"
    book.close()
