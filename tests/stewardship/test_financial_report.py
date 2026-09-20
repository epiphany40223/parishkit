"""Database-free financial detail grammar, exact money and row shaping."""

import json
from contextlib import contextmanager
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.reports import financial
from parishkit.stewardship.reports.financial import (
    FinancialQuery,
    financial_page,
    money,
    share_labels,
)

STAFF = Principal(uuid4(), frozenset({"staff"}), frozenset())
LEADER = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({9}))
CONFIGURATION = {
    "year_label": "2027",
    "financial": {"start": "2027-01-01", "end": "2027-12-31"},
}
OPTIONS = [
    {"id": "online", "label": "{{ pronoun }} will give online to {{ parish_name }}"},
    {"id": "other", "label": "Another way in {{ campaign_year }}"},
]


def test_query_accepts_only_the_closed_bounded_grammar():
    """The application refuses what SQL would refuse, before any statement."""
    query = FinancialQuery.parse(
        MultiValueDict(
            {
                "search": ["Example"],
                "pledge_min": ["0"],
                "pledge_max": ["999999999.99"],
                "first_start": ["2026-01-01"],
                "first_end": ["2026-01-01"],
                "share": ["option-1.a:b_c"],
                "sort": ["pledge_desc"],
                "page": ["3"],
            }
        )
    )
    assert query.page == 3 and query.share == "option-1.a:b_c"
    # The page is navigation, never part of the filter identity.
    assert "page" not in query.form_values()
    assert FinancialQuery.parse({}) == FinancialQuery()
    assert FinancialQuery().form_values() == {
        "search": "",
        "active": "any",
        "first_start": "",
        "first_end": "",
        "latest_start": "",
        "latest_end": "",
        "pledge_min": "",
        "pledge_max": "",
        "amount": "any",
        "frequency": "any",
        "share": "any",
        "sort": "name",
    }
    for values in (
        {"pledge_min": "1,234"},
        {"pledge_min": "12.5"},
        {"pledge_min": "-1"},
        {"pledge_min": "01"},
        {"pledge_min": "1e3"},
        {"pledge_max": "1234567890"},
        {"pledge_min": "5", "pledge_max": "4.99"},
        {"latest_start": "2026-02-30"},
        {"latest_start": "20260201"},
        {"first_start": "2026-02-01", "first_end": "2026-01-31"},
        {"active": "yes"},
        {"amount": "all"},
        {"frequency": "biweekly"},
        {"share": "bad share!"},
        {"share": "Q" * 65},
        {"sort": "random"},
        {"page": "0"},
        {"unknown": "Q"},
        {"search": 5},
    ):
        with pytest.raises(ValueError):
            FinancialQuery.parse(values)
    with pytest.raises(ValueError):
        FinancialQuery.parse(MultiValueDict({"sort": ["name", "newest"]}))


def test_money_is_parsed_exactly_and_absence_stays_unavailable():
    """Canonical SQL text never passes through a binary float."""
    assert money("1234.50").cents == 123450 and money("1234.50").display == "$1,234.50"
    assert money("0.10").cents + money("0.20").cents == money("0.30").cents
    assert money("999999999999.99").cents == 99999999999999
    assert money("7").cents == 700
    assert not money(None).available and money(None).display == "Unavailable"


def test_share_labels_use_the_neutral_household_wording():
    """A report addresses no single household, and evaluates no template later."""
    labels = share_labels(OPTIONS, configuration=CONFIGURATION, parish_name="A <B>")
    assert labels == {
        "online": "This household will give online to A <B>",
        "other": "Another way in 2027",
    }
    # No year label falls back to the campaign start; no options render nothing.
    started = {"start_date": "2028-03-01"}
    assert share_labels(OPTIONS[1:], configuration=started, parish_name="Sample") == {
        "other": "Another way in 2028"
    }
    assert share_labels(None, configuration=started, parish_name="Sample") == {}


class Cursor:
    """Stand in for the one statement the read model issues."""

    def __init__(self, answer):
        self.answer, self.statements = answer, []

    def execute(self, statement, parameters):
        """Retain the exact parameters for the closed-shape assertion."""
        self.statements.append((statement, parameters))

    def fetchone(self):
        """Return the function's single text column."""
        return self.answer


def substitute(monkeypatch, answer):
    """Replace only this module's connection; no database is opened."""
    cursor = Cursor(answer)

    class Connection:
        @contextmanager
        def cursor(self):
            yield cursor

    monkeypatch.setattr(financial, "connection", Connection())
    return cursor


def page(principal=STAFF, *, giving=True, query=None):
    """Read with the fixed test configuration."""
    return financial_page(
        uuid4(),
        query or FinancialQuery(),
        principal,
        giving=giving,
        parish_name="Sample Parish",
        configuration=CONFIGURATION,
    )


def result(**row):
    """Build the closed SQL answer around one row."""
    moment = "2026-09-19T15:04:00+00:00"
    values = dict(
        id=str(uuid4()),
        family_name="Example",
        family_duid=7,
        active=True,
        submitted_at=moment,
        first_submitted_at=moment,
        family_version=1,
        annual_pledge="1234.50",
        frequency="monthly",
        shares={"online": "", "retired": "Old text"},
        share_options=OPTIONS,
        pledge_total="1200.00",
        contribution_total=None,
    )
    return {
        "metadata": {
            "source_as_of": moment,
            "giving_through": "2026-06-30",
            "share_options": OPTIONS,
        },
        "summary": {
            "families": 1,
            "annual_total": "1234.50",
            "frequencies": {"monthly": 1},
            "shares": {"online": 1, "retired": 1},
            "no_share": 0,
        },
        "total": 1,
        "rows": [values | row],
    }


def test_rows_are_shaped_with_exact_installments_and_honest_absence(monkeypatch):
    """Half-up installments, versioned labels and unavailable source money."""
    cursor = substitute(monkeypatch, (json.dumps(result()),))
    shaped = page(query=FinancialQuery(search="Example", page=2))
    ((_, parameters),) = cursor.statements
    assert parameters[2] == 2
    assert json.loads(parameters[1]) == {
        "filters": FinancialQuery(search="Example").form_values(),
        "giving": True,
    }
    row = shaped["rows"][0]
    # 1,234.50 / 12 is 102.875 exactly; half-up gives 102.88, never 102.87.
    assert row["installment"].display == "$102.88"
    assert row["frequency_label"] == "Monthly"
    assert row["shares"] == [
        {"label": "This household will give online to Sample Parish", "text": ""},
        # An option absent from the configuration the Family saw is never guessed.
        {"label": "Unavailable share method", "text": "Old text"},
    ]
    assert row["source_pledge"].display == "$1,200.00"
    assert not row["source_contributions"].available
    assert "share_options" not in row and "pledge_total" not in row
    assert row["submitted_at"] == datetime(2026, 9, 19, 15, 4, tzinfo=UTC)
    assert shaped["metadata"]["giving_through"] == date(2026, 6, 30)
    assert shaped["summary"]["annual_total"].cents == 123450
    assert dict(shaped["summary"]["frequencies"]) == {
        "Weekly": 0,
        "Monthly": 1,
        "Quarterly": 0,
        "Annual": 0,
        "No frequency": 0,
    }
    assert ("Unavailable share method", 1) in shaped["summary"]["shares"]
    assert shaped["share_choices"] == [
        ("other", "Another way in 2027"),
        ("online", "This household will give online to Sample Parish"),
    ]

    substitute(monkeypatch, (json.dumps(result(annual_pledge="0.00", frequency="")),))
    zero = page()["rows"][0]
    assert zero["annual"].display == "$0.00" and not zero["installment"].available
    assert zero["frequency_label"] == "No frequency"


def test_denial_and_unavailable_inputs_never_become_an_empty_report(monkeypatch):
    """A leader issues no statement; disabled and missing inputs are distinct."""
    cursor = substitute(monkeypatch, (json.dumps(result()),))
    with pytest.raises(PermissionError):
        page(LEADER)
    with pytest.raises(TypeError):
        page(giving=None)
    assert cursor.statements == []
    substitute(monkeypatch, (json.dumps({"disabled": True}),))
    with pytest.raises(PermissionError):
        page()
    for answer in ((json.dumps({"unavailable": True}),), (None,), None):
        substitute(monkeypatch, answer)
        with pytest.raises(ReadUnavailable):
            page()
