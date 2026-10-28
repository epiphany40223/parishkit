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
from parishkit.stewardship.web.dates import UnknownZone
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
CONFIGURATION = configuration() | {"year_label": "2055", "share_options": OPTIONS}
# The configuration an earlier Family answered, before the year label was edited.
EARLIER = CONFIGURATION | {"year_label": "Jubilee"}
SEEN = str(uuid4())
PERIOD = f"{parish_date(date(2055, 1, 1))} – {parish_date(date(2055, 12, 31))}"
PROOF = {"snapshot": str(uuid4()), "configuration": str(uuid4())}
SOURCE = PROOF["snapshot"]


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
                "zone": ["America/Los_Angeles"],
                "share": [OTHER],
                "sort": ["pledge_desc"],
                "page": ["3"],
            }
        )
    )
    assert query.page == 3 and query.share == OTHER
    assert query.zone == "America/Los_Angeles"
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
        "zone": "",
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


@pytest.mark.parametrize(
    "name", ["first_start", "first_end", "latest_start", "latest_end"]
)
@pytest.mark.parametrize("zone", [None, "", "Mars/Olympus", "UTC+5", "../etc/zone"])
def test_a_date_needs_the_browsers_zone(name, zone):
    """Each response date is a day in the browser's zone (#558): without a
    catalog zone it is refused as UnknownZone, never read in another zone."""
    values = {name: "2026-03-08"} | ({} if zone is None else {"zone": zone})
    with pytest.raises(UnknownZone):
        FinancialQuery.parse(values)
    query = FinancialQuery.parse(values | {"zone": "Asia/Kathmandu"})
    assert query.form_values()[name] == "2026-03-08"
    assert query.form_values()["zone"] == "Asia/Kathmandu"


def test_a_zone_without_dates_is_unused():
    """Without dates the zone matters to nothing: a catalog zone is kept (it
    travels with the filters) and an unknown one is dropped, not refused."""
    assert FinancialQuery.parse({"zone": "Mars/Olympus"}).zone == ""
    assert FinancialQuery.parse({"zone": "America/Chicago"}).zone == "America/Chicago"
    assert FinancialQuery.parse({"zone": "", "pledge_min": "5"}).pledge_min == "5"


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
        OTHER: f"Another way in 2055, {PERIOD}",
    }
    # No year label falls back to the commitment period's year.
    unlabeled = CONFIGURATION | {"year_label": None}
    assert (
        share_labels(unlabeled, campaign_id=CAMPAIGN, parish_name="Sample")[OTHER]
        == f"Another way in 2055, {PERIOD}"
    )
    # An unusable retained configuration words nothing rather than guessing.
    assert share_labels({}, campaign_id=CAMPAIGN, parish_name="Sample") == {}


def test_a_table_cell_names_a_share_method_by_its_short_label():
    """A cell shows the short label, the full label its tooltip and its
    screen-reader text; a method without one shows its label alone; Other
    text follows, everything escaped; files keep the full labels (#2198)."""
    online = {"label": "Online <giving>", "short": "Online & co", "text": ""}
    other = {"label": "Other", "short": "Other", "text": "<b>Bonds</b>"}
    assert str(financial.share_name(online)) == (
        '<span title="Online &lt;giving&gt;"><span aria-hidden="true">'
        'Online &amp; co</span><span class="visually-hidden">Online &lt;giving&gt;'
        "</span></span>"
    )
    assert str(financial.share_name(other)) == "Other"
    assert str(financial.share_cell([other, other], "; ")) == (
        "Other: &lt;b&gt;Bonds&lt;/b&gt;; Other: &lt;b&gt;Bonds&lt;/b&gt;"
    )
    assert financial.share_cell([]) == ""
    assert financial.share_text([online, other]) == (
        "Online <giving>, Other: <b>Bonds</b>"
    )
    # The template filter Financial stewardship's chips use is the same
    # markup, escaped once.
    from django.template import engines

    drawn = (
        engines["django"]
        .from_string("{% load stewardship %}{{ share|share_name }}")
        .render({"share": online})
    )
    assert drawn == str(financial.share_name(online))


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
    # The directory's name from the snapshot SQL read, without a database.
    cursor.named = []

    def name_file_rows(metadata, rows):
        """Record the snapshot and add the heads, as the shared helper does."""
        cursor.named.append(metadata["source_id"])
        for row in rows:
            row["family_name"] += ", Ann and Bo"

    monkeypatch.setattr(financial, "name_file_rows", name_file_rows)
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
        "metadata": {
            "source_id": SOURCE,
            "source_as_of": moment,
            "giving_through": "2026-06-30",
            "comparison_start": "2025-07-01",
            "comparison_end": "2026-06-30",
        },
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
        # Without a short label the page's cell shows the full label (#1569).
        {
            "id": OTHER,
            "label": f"Another way in Jubilee, {PERIOD}",
            "short": f"Another way in Jubilee, {PERIOD}",
            "text": "Stock",
        },
        # An option absent from the configuration the Family saw is never guessed.
        {
            "id": RETIRED,
            "label": "Unavailable share method",
            "short": "Unavailable share method",
            "text": "Old text",
        },
    ]
    assert row["source_pledge"].display == "$1,200.00"
    assert not row["source_contributions"].available
    assert "configuration_id" not in row and "pledge_total" not in row
    assert row["submitted_at"] == datetime(2026, 9, 19, 15, 4, tzinfo=UTC)
    # Named as in every Admin table, from the snapshot SQL read (#932).
    assert row["family_name"] == "Example, Ann and Bo"
    assert cursor.named == [SOURCE]
    assert shaped["metadata"]["giving_through"] == date(2026, 6, 30)
    # The ParishSoft headings name the comparison period's years.
    assert shaped["metadata"]["comparison_years"] == "2025–2026"
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
        (f"Another way in 2055, {PERIOD}", 1),
        ("Unavailable share method", 3),
    ]
    # The filter lists current options in the order the parish configured them;
    # their identities are opaque, so sorting by them would read as random.
    assert shaped["share_choices"] == [
        (ONLINE, "This household will give online to Sample Parish"),
        (OTHER, f"Another way in 2055, {PERIOD}"),
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
        (f"Another way in 2055, {PERIOD}", 1),
        (f"Another way in 2055, {PERIOD}", 1),
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


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2026-01-01", "2026-12-31", "2026"),
        ("2025-07-01", "2026-06-30", "2025–2026"),
        (None, "2026-12-31", ""),
        ("2026-01-01", None, ""),
    ],
)
def test_period_years_word_the_comparison_period_as_the_family_form(
    start, end, expected
):
    """One year, a span of two, or nothing when the campaign has no period."""
    assert financial.period_years(start, end) == expected


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


def test_page_address_keeps_only_the_closed_choices():
    """The address bar names the view's closed choices, never a private one.

    A reload keeps Family status, Pledge amount, Frequency, Share method, the
    sort and the rows per page (UX conventions, "Reload on a POST report",
    #1586); the search, the dates, the pledge range and the page number stay
    in the POST body, and every default is left out.
    """
    from parishkit.stewardship.reports.financial_views import page_address

    route = "/admin/reports/financial/"
    assert page_address(route, FinancialQuery(), 50) == route
    query = FinancialQuery.parse(
        {
            "search": "Private name",
            "active": "inactive",
            "amount": "nonzero",
            "frequency": "monthly",
            "share": ONLINE,
            "sort": "pledge_desc",
            "first_start": "2055-01-01",
            "zone": "America/New_York",
            "pledge_min": "100",
            "pledge_max": "500",
            "page": "3",
        }
    )
    assert page_address(route, query, 100) == (
        f"{route}?active=inactive&amount=nonzero&frequency=monthly"
        f"&share={ONLINE}&sort=pledge_desc&size=100"
    )
    assert page_address(route, FinancialQuery(share="none"), 25) == (
        f"{route}?share=none&size=25"
    )


@pytest.mark.parametrize(
    "query",
    [
        "search=Private",
        "pledge_min=100",
        "first_start=2055-01-01",
        "zone=America/New_York",
        "page=2",
        "extra=x",
    ],
)
def test_a_get_carries_only_the_page_address_fields(rf, query):
    """A GET with any field the page's own address never carries is refused."""
    from parishkit.stewardship.reports.financial_views import _parameters

    with pytest.raises(ValueError):
        _parameters(rf.get("/admin/reports/financial/?amount=zero&" + query))


def test_a_get_draws_the_view_its_address_names(rf):
    """The page's own address parses into the same view a POST drew."""
    from parishkit.stewardship.reports.financial_views import _parameters

    parameters = _parameters(
        rf.get(f"/?active=active&frequency=weekly&share={ONLINE}&sort=pledge&size=25")
    )
    assert parameters.pop("size") == ["25"]
    query = FinancialQuery.parse(parameters)
    assert (query.active, query.frequency, query.share, query.sort) == (
        "active",
        "weekly",
        ONLINE,
        "pledge",
    )
    # A POST still carries its private filters, without its CSRF token.
    posted = _parameters(rf.post("/", {"search": "Name", "csrfmiddlewaretoken": "t"}))
    assert dict(posted) == {"search": ["Name"]}
    with pytest.raises(ValueError):
        _parameters(rf.post("/?amount=zero", {"search": "Name"}))


def test_short_labels_name_methods_on_the_page_but_never_in_exports(monkeypatch):
    """A short label (#1569) names a method in the page's cell, its filter and
    its chart; the full label stays beside it for the exports and summary."""
    from parishkit.stewardship.reports.chart_specs import financial_charts

    shortened = CONFIGURATION | {
        "share_options": [OPTIONS[0] | {"short_label": "Online"}, OPTIONS[1]]
    }
    substitute(monkeypatch, (json.dumps(result(shares={ONLINE: "", OTHER: "x"})),))
    monkeypatch.setattr(
        financial,
        "seen_configurations",
        lambda campaign_id, identifiers: dict.fromkeys(identifiers, shortened),
    )
    shaped = page(configuration=shortened)
    full = "This household will give online to Sample Parish"
    assert shaped["rows"][0]["shares"] == [
        {"id": ONLINE, "label": full, "short": "Online", "text": ""},
        # No short label: the full label in both places.
        {
            "id": OTHER,
            "label": f"Another way in 2055, {PERIOD}",
            "short": f"Another way in 2055, {PERIOD}",
            "text": "x",
        },
    ]
    assert shaped["share_choices"] == [
        (ONLINE, "Online"),
        (OTHER, f"Another way in 2055, {PERIOD}"),
    ]
    # The exports' summary keeps the full wording.
    assert shaped["summary"]["shares"][0] == (f"Another way in 2055, {PERIOD}", 1)
    _frequency, shares = financial_charts(shaped["summary"], shaped["share_choices"])
    assert [row[0] for row in shares.table.rows][:2] == [
        "Online",
        f"Another way in 2055, {PERIOD}",
    ]


def test_a_short_label_set_later_names_earlier_answers(monkeypatch):
    """A short label set after a Family answered (on a live campaign, #1569)
    names the method in that Family's row too; the full label stays the one
    the Family saw."""
    shortened = CONFIGURATION | {
        "share_options": [OPTIONS[0], OPTIONS[1] | {"short_label": "Other"}]
    }
    substitute(monkeypatch, (json.dumps(result()),))
    row = page(configuration=shortened)["rows"][0]
    assert row["shares"][0] == {
        "id": OTHER,
        "label": f"Another way in Jubilee, {PERIOD}",
        "short": "Other",
        "text": "Stock",
    }


def test_a_cleared_short_label_shows_the_full_label_for_earlier_answers(monkeypatch):
    """Clearing a short label (#1569) names the method by its full label in
    every row, also for answers given while the short label was set: the
    short label is the Admin's current name, never the one the Family saw."""
    earlier = CONFIGURATION | {
        "share_options": [OPTIONS[0], OPTIONS[1] | {"short_label": "Other"}]
    }
    substitute(monkeypatch, (json.dumps(result()),))
    monkeypatch.setattr(
        financial,
        "seen_configurations",
        lambda campaign_id, identifiers: dict.fromkeys(identifiers, earlier),
    )
    row = page(configuration=CONFIGURATION)["rows"][0]
    full = f"Another way in 2055, {PERIOD}"
    assert row["shares"][0] == {
        "id": OTHER,
        "label": full,
        "short": full,
        "text": "Stock",
    }


# The Family's own answer beside an Edit in force (0182, #2196), as the
# selection returns it.
OWN = {
    "annual_pledge": "1234.50",
    "frequency": "monthly",
    "cannot_give": False,
    "shares": {OTHER: "Stock"},
}


def test_an_edit_in_force_is_marked_and_words_the_familys_answer(monkeypatch):
    """A row with an Edit in force (#2196) is marked, words the reviewer's
    share methods by the current options first (as Pledges does), and words
    the Family's own answer as the Family saw it for the page's tip."""
    edited = result(
        annual_pledge="2000.00",
        frequency="annual",
        shares={ONLINE: "", RETIRED: ""},
        edited=True,
        family_answer=OWN,
    )
    substitute(monkeypatch, (json.dumps(edited),))
    row = page()["rows"][0]
    assert row["edited"] is True and "family_answer" not in row
    assert row["annual"] == parse_money("2000.00")
    assert row["frequency_label"] == "Annual"
    assert row["shares"] == [
        {
            "id": ONLINE,
            "label": "This household will give online to Sample Parish",
            "short": "This household will give online to Sample Parish",
            "text": "",
        },
        # Neither offered now nor in what the Family saw: never guessed.
        {
            "id": RETIRED,
            "label": "Unavailable share method",
            "short": "Unavailable share method",
            "text": "",
        },
    ]
    assert row["family_answer_text"] == (
        f"$1,234.50, Monthly; Another way in Jubilee, {PERIOD}: Stock"
    )


def test_an_edit_that_kept_the_share_methods_words_them_as_the_family_saw(
    monkeypatch,
):
    """An Edit of the amount alone carries the Family's own share methods,
    which keep the wording the Family saw; a zero edit of a Family that
    cannot contribute words its answer so."""
    kept = result(annual_pledge="1500.00", edited=True, family_answer=OWN)
    kept["rows"][0]["shares"] = OWN["shares"]
    substitute(monkeypatch, (json.dumps(kept),))
    row = page()["rows"][0]
    assert [share["label"] for share in row["shares"]] == [
        f"Another way in Jubilee, {PERIOD}"
    ]
    cannot = OWN | {"annual_pledge": "0.00", "frequency": None, "cannot_give": True}
    assert financial.answer_text(cannot, []) == "Cannot contribute"
    assert financial.answer_text(OWN | {"shares": {}}, []) == "$1,234.50, Monthly"


def test_rows_without_an_edit_or_from_an_older_capture_are_unmarked(monkeypatch):
    """No Edit in force reads False; a capture made before 0182 has no
    marker (None, blank in the files) and no tip."""
    substitute(monkeypatch, (json.dumps(result(edited=False, family_answer=None)),))
    row = page()["rows"][0]
    assert (row["edited"], row["family_answer_text"]) == (False, "")
    substitute(monkeypatch, (json.dumps(result()),))
    row = page()["rows"][0]
    assert (row["edited"], row["family_answer_text"]) == (None, "")


def test_family_answers_put_the_familys_own_answer_back():
    """Pledges and the hub read the Family's answer (#2196): the values in
    force are replaced by it, and the marker is dropped; a row without an
    Edit is left as it is."""
    edited = result(
        annual_pledge="2000.00",
        frequency="annual",
        cannot_give=False,
        shares={ONLINE: ""},
        edited=True,
        family_answer=OWN,
    )
    plain = result(edited=False, family_answer=None)
    rows = financial.family_answers(edited)["rows"]
    assert {key: rows[0][key] for key in OWN} == OWN
    assert "edited" not in rows[0] and "family_answer" not in rows[0]
    (unchanged,) = financial.family_answers(plain)["rows"]
    assert unchanged["annual_pledge"] == "1234.50" and "edited" not in unchanged
