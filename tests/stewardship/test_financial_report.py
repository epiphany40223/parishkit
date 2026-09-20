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
    parse_money,
    share_labels,
)
from parishkit.stewardship.web.presentation import parish_date

from .financial_factory import CAMPAIGN, configuration

STAFF = Principal(uuid4(), frozenset({"staff"}), frozenset())
LEADER = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({9}))
# Stable share-option identities are UUIDs; two more are no longer offered.
ONLINE, OTHER, RETIRED, REMOVED = (
    f"{n}abcdef0-0000-4000-8000-000000000000" for n in "1234"
)
OPTIONS = [
    {
        "id": ONLINE,
        "label": "{{ pronoun }} will give online to {{ parish_name }}",
        "free_text": False,
    },
    {
        "id": OTHER,
        "label": "Another way in {{ campaign_year }}, {{ financial_period }}",
        "free_text": True,
    },
]
CONFIGURATION = configuration() | {"year_label": "2027", "share_options": OPTIONS}
# The configuration an earlier Family answered, before the year label was edited.
EARLIER = CONFIGURATION | {"year_label": "Jubilee"}
SEEN = str(uuid4())
PERIOD = f"{parish_date(date(2027, 1, 1))} – {parish_date(date(2027, 12, 31))}"
PROOF = {"snapshot": str(uuid4()), "configuration": str(uuid4())}


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
                "share": [OTHER],
                "sort": ["pledge_desc"],
                "page": ["3"],
            }
        )
    )
    assert query.page == 3 and query.share == OTHER
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
        {"share": "online"},
        {"share": OTHER.upper()},
        {"share": OTHER + "0"},
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
    assert (
        parse_money("1234.50").cents == 123450
        and parse_money("1234.50").display == "$1,234.50"
    )
    assert (
        parse_money("0.10").cents + parse_money("0.20").cents
        == parse_money("0.30").cents
    )
    assert parse_money("999999999999.99").cents == 99999999999999
    assert parse_money("7").cents == 700
    assert (
        not parse_money(None).available and parse_money(None).display == "Unavailable"
    )


def test_share_labels_use_the_neutral_household_wording():
    """A report addresses no single household, and evaluates no template later."""
    labels = share_labels(CONFIGURATION, campaign_id=CAMPAIGN, parish_name="A <B>")
    assert labels == {
        ONLINE: "This household will give online to A <B>",
        # The Family form's own validated upcoming period, not a second rule.
        OTHER: f"Another way in 2027, {PERIOD}",
    }
    # No year label falls back to the campaign start year.
    unlabeled = CONFIGURATION | {"year_label": None}
    assert (
        share_labels(unlabeled, campaign_id=CAMPAIGN, parish_name="Sample")[OTHER]
        == f"Another way in 2026, {PERIOD}"
    )
    # An unusable retained configuration words nothing rather than guessing.
    assert share_labels({}, campaign_id=CAMPAIGN, parish_name="Sample") == {}


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
    # The immutable configuration the row's Family answered, without a database.
    monkeypatch.setattr(
        financial,
        "seen_configurations",
        lambda campaign_id, identifiers: {
            key: EARLIER for key in identifiers if key == SEEN
        },
    )
    return cursor


def page(principal=STAFF, *, proof=PROOF, query=None, configuration=CONFIGURATION):
    """Read with the fixed current configuration unless a case replaces it."""
    return financial_page(
        CAMPAIGN,
        query or FinancialQuery(),
        principal,
        proof=proof,
        parish_name="Sample Parish",
        configuration=configuration,
        page_size=financial.PAGE_SIZE,
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
        shares={OTHER: "Stock", RETIRED: "Old text"},
        configuration_id=SEEN,
        pledge_total="1200.00",
        contribution_total=None,
    )
    return {
        "metadata": {"source_as_of": moment, "giving_through": "2026-06-30"},
        "summary": {
            "families": 1,
            "annual_total": "1234.50",
            "frequencies": {"monthly": 1},
            "shares": {OTHER: 1, RETIRED: 1, REMOVED: 2},
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
    # SQL pages with the caller's own size, so the two can never drift apart.
    assert parameters[2:] == [2, financial.PAGE_SIZE]
    assert json.loads(parameters[1]) == {
        "filters": FinancialQuery(search="Example").form_values(),
        # The proof names what it proved; SQL alone decides whether it applies.
        "proof": PROOF,
    }
    row = shaped["rows"][0]
    # 1,234.50 / 12 is 102.875 exactly; half-up gives 102.88, never 102.87.
    assert row["installment"].display == "$102.88"
    assert row["frequency_label"] == "Monthly"
    assert row["shares"] == [
        # Worded as that Family saw it, before the year label was later edited.
        {"label": f"Another way in Jubilee, {PERIOD}", "text": "Stock"},
        # An option absent from the configuration the Family saw is never guessed.
        {"label": "Unavailable share method", "text": "Old text"},
    ]
    assert row["source_pledge"].display == "$1,200.00"
    assert not row["source_contributions"].available
    assert "configuration_id" not in row and "pledge_total" not in row
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
    # The whole-result summary and the filter use the current wording, and count
    # options no longer offered together rather than guessing their names.
    assert shaped["summary"]["shares"] == [
        (f"Another way in 2027, {PERIOD}", 1),
        ("Unavailable share method", 3),
    ]
    # The filter lists current options in the order the parish configured them;
    # their identities are opaque, so sorting by them would read as random.
    assert shaped["share_choices"] == [
        (ONLINE, "This household will give online to Sample Parish"),
        (OTHER, f"Another way in 2027, {PERIOD}"),
    ]
    both = result(shares={OTHER: "", ONLINE: ""})
    substitute(monkeypatch, (json.dumps(both),))
    assert [share["label"][:7] for share in page()["rows"][0]["shares"]] == [
        "This ho",
        "Another",
    ]
    # Two offered options worded alike stay separate counts: only identities no
    # longer offered are merged, never distinct current options.
    alike = CONFIGURATION | {
        "share_options": [*OPTIONS, OPTIONS[1] | {"id": RETIRED}],
    }
    substitute(monkeypatch, (json.dumps(result()),))
    assert page(configuration=alike)["summary"]["shares"] == [
        (f"Another way in 2027, {PERIOD}", 1),
        (f"Another way in 2027, {PERIOD}", 1),
        ("Unavailable share method", 2),
    ]
    # A row whose configuration cannot be found words nothing, never current text.
    substitute(monkeypatch, (json.dumps(result(configuration_id=str(uuid4()))),))
    assert {share["label"] for share in page()["rows"][0]["shares"]} == {
        "Unavailable share method"
    }

    substitute(monkeypatch, (json.dumps(result(annual_pledge="0.00", frequency="")),))
    zero = page()["rows"][0]
    assert zero["annual"].display == "$0.00" and not zero["installment"].available
    assert zero["frequency_label"] == "No frequency"
    # A zero pledge may omit its frequency but is allowed to carry one; then the
    # installment is a real zero rather than unavailable.
    substitute(monkeypatch, (json.dumps(result(annual_pledge="0.00")),))
    kept = page()["rows"][0]
    assert kept["frequency_label"] == "Monthly"
    assert kept["installment"].available and kept["installment"].display == "$0.00"


def test_denial_and_unavailable_inputs_never_become_an_empty_report(monkeypatch):
    """A leader issues no statement; disabled and missing inputs are distinct."""
    cursor = substitute(monkeypatch, (json.dumps(result()),))
    with pytest.raises(PermissionError):
        page(LEADER)
    for malformed in (True, {"snapshot": PROOF["snapshot"]}, PROOF | {"extra": 1}):
        with pytest.raises(TypeError):
            page(proof=malformed)
    assert cursor.statements == []
    # No proof is a legitimate request: SQL simply withholds comparison money.
    page(proof=None)
    assert json.loads(cursor.statements[0][1][1])["proof"] is None
    substitute(monkeypatch, (json.dumps({"disabled": True}),))
    with pytest.raises(PermissionError):
        page()
    for answer in ((json.dumps({"unavailable": True}),), (None,), None):
        substitute(monkeypatch, answer)
        with pytest.raises(ReadUnavailable):
            page()
