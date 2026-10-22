"""The report block registry and its byte-identical gate (#1786).

The report-emails spec's delivery plan (item 2) moved the Daily and Weekly
digest content into blocks drawn by one compiler, with no visible change.
These tests compile each digest both ways, the new compiler and a frozen
copy of the old one (``legacy_digest_compilers``), and require the same
subject, HTML, text and images byte for byte, then the same envelope
(subject, intro and Testing heading) for Production and Testing sends
(report-emails spec, "Gating tests", without the later scope change).
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from parishkit.stewardship.jobs.digest_content import (
    DigestTemplate,
    render_digest_envelope,
)
from parishkit.stewardship.reports import chart_rendering
from parishkit.stewardship.reports.daily_digest import (
    DAILY_BLOCKS,
    render_daily_digest,
)
from parishkit.stewardship.reports.digest_comparison import section
from parishkit.stewardship.reports.report_blocks import REGISTRY
from parishkit.stewardship.reports.report_blocks.compiler import (
    compile_report,
    report_blocks,
)
from parishkit.stewardship.reports.report_blocks.registry import (
    AFTER_BUTTON,
    BODY,
    LIST,
    STATUS,
)
from parishkit.stewardship.reports.weekly_digest import (
    WEEKLY_BLOCKS,
    WeeklyCorrection,
    WeeklyInformation,
    render_weekly_digest,
)
from parishkit.stewardship.web import dates

from .legacy_digest_compilers import (
    legacy_render_daily_digest,
    legacy_render_weekly_digest,
)
from .test_daily_digest_content import document as daily_document
from .test_daily_digest_content import funnel_metrics, long_document
from .test_digest_comparison import found, png
from .test_digest_content import arguments
from .test_weekly_digest_content import INSTANT
from .test_weekly_digest_content import document as weekly_document

ORIGIN = "https://campaign.example.org"


@pytest.fixture
def renderer(monkeypatch):
    """Stand in for the chart engine's server renderer with a fixed PNG."""

    def render_chart(spec, *, format, what, limit_seconds=60):
        """One PNG at the email width, the same bytes for both compilers."""
        return chart_rendering.ChartImage(png(), "png", 880, 310)

    monkeypatch.setattr(chart_rendering, "render_chart", render_chart)


def recovery():
    """A recovery Daily covering the last eight of twelve campaign days."""
    value = long_document(12)
    last = value.participation.last_date
    covered = tuple(last - timedelta(days=offset) for offset in range(7, -1, -1))
    return replace(value, covered_dates=covered)


def no_financial():
    """A Daily without financial stewardship: no pledge column or total."""
    value = daily_document()
    chart = replace(value.participation, financial_enabled=False)
    statistics = replace(value.statistics, financial_enabled=False)
    return replace(value, participation=chart, statistics=statistics)


def gaps(*, report_day):
    """A recovery Daily with an unavailable day and a zero-Families day.

    With ``report_day`` the report day itself is unavailable, so its totals
    draw no bar and no busiest-day note; otherwise an earlier day is.
    """
    value = daily_document()
    days = value.participation.days
    missing = replace(
        days[0],
        first_responses=0,
        cumulative_responses=0,
        cohort_denominator=0,
        source_generation=None,
        source_as_of=None,
        population_available=False,
        pledge_available=False,
        pledge_total=None,
    )
    zero = replace(
        days[1],
        first_responses=0,
        cumulative_responses=0,
        cohort_denominator=0,
        pledge_total=Decimal("0.00"),
    )
    series = (missing, zero, days[2])
    if report_day:
        series = (*days[:2], replace(missing, local_date=days[2].local_date))
    return replace(
        value,
        covered_dates=tuple(day.local_date for day in days),
        participation=replace(value.participation, days=series),
    )


def with_comparison(**options):
    """A Production Daily with the Compared with past campaigns section."""
    return replace(
        daily_document(),
        funnel=funnel_metrics(),
        mode="production",
        comparison=section(found(**options)),
    )


DAILY_CASES = {
    "single day, no mode": daily_document,
    "single day, production funnel": lambda: replace(
        daily_document(), funnel=funnel_metrics(), mode="production"
    ),
    "single day, testing": lambda: replace(daily_document(), mode="testing"),
    "recovery range": lambda: replace(recovery(), mode="production"),
    "long campaign": lambda: long_document(30),
    "no financial stewardship": no_financial,
    "unavailable and zero days": lambda: gaps(report_day=False),
    "unavailable report day": lambda: gaps(report_day=True),
    "pinned date format": lambda: replace(daily_document(), date_format="eu_dot"),
    "comparison": with_comparison,
    "comparison without financial": lambda: with_comparison(financial=False),
}


def same_daily(value, *, charts=True):
    """Compile ``value`` both ways and require identical contents."""
    new = render_daily_digest(value, public_origin=ORIGIN, charts=charts)
    old = legacy_render_daily_digest(value, public_origin=ORIGIN, charts=charts)
    assert (new.subject, new.html, new.text) == (old.subject, old.html, old.text)
    assert (new.chart.content_id, new.chart.data) == (
        old.chart.content_id,
        old.chart.data,
    )
    assert [(i.content_id, i.data) for i in new.comparison] == [
        (i.content_id, i.data) for i in old.comparison
    ]
    assert new.funnel == old.funnel == ()
    assert new.charts_withheld is old.charts_withheld is (not charts)
    return new, old


@pytest.mark.parametrize("case", DAILY_CASES)
def test_daily_digest_is_byte_identical(case, renderer):
    """Every Daily case compiles to the old compiler's exact bytes."""
    same_daily(DAILY_CASES[case]())


def test_withheld_charts_daily_is_byte_identical(renderer):
    """A last-attempt compile without server charts keeps every byte too."""
    new, _old = same_daily(with_comparison(), charts=False)
    assert new.comparison == ()


def test_the_ambient_date_format_does_not_leak_in():
    """The new compiler pins the document's style over the worker's active one."""
    daily, weekly = daily_document(), weekly_document()
    expected = (
        render_daily_digest(daily, public_origin=ORIGIN).text,
        render_weekly_digest(weekly, public_origin=ORIGIN),
    )
    with dates.using("iso"):
        same_daily(daily)
        assert (
            render_daily_digest(daily, public_origin=ORIGIN).text,
            render_weekly_digest(weekly, public_origin=ORIGIN),
        ) == expected


def information(number, text="Please call us.", days=0):
    """One actionable request, ``days`` before the capture instant."""
    return WeeklyInformation(
        UUID(int=100 + number),
        1000 + number,
        f"Family {number}",
        INSTANT - timedelta(days=days),
        text,
    )


WEEKLY_CASES = {
    "scheduled": weekly_document,
    "manual": lambda: replace(weekly_document(), manual=True),
    "testing": lambda: replace(weekly_document(), mode="testing"),
    "production funnel": lambda: replace(
        weekly_document(), mode="production", funnel=funnel_metrics()
    ),
    "requests only": lambda: replace(
        weekly_document(),
        information=tuple(information(n, days=5 - n) for n in range(3)),
        corrections=(),
    ),
    "corrections only": lambda: replace(weekly_document(), information=()),
    "one correction": lambda: replace(
        weekly_document(),
        information=(),
        corrections=(
            WeeklyCorrection(UUID(int=9), 90, "E family", INSTANT, "withdrawn"),
        ),
    ),
    "long and escaped text": lambda: replace(
        weekly_document(),
        information=(
            information(1, "word " * 80, days=1),
            information(2, '<b>"Quoted" & {{ code }}</b>'),
        ),
    ),
    "pinned date format": lambda: replace(weekly_document(), date_format="iso"),
    "imported whitespace in names": lambda: replace(
        weekly_document(),
        information=(
            replace(information(1), family_name="Smith\u00a0and\r\nJones  Family"),
        ),
    ),
    "another time zone": lambda: replace(
        weekly_document(), campaign_timezone="America/Los_Angeles"
    ),
}


@pytest.mark.parametrize("case", WEEKLY_CASES)
def test_weekly_digest_is_byte_identical(case):
    """Every Weekly case compiles to the old compiler's exact bytes."""
    value = WEEKLY_CASES[case]()
    new = render_weekly_digest(value, public_origin=ORIGIN)
    old = legacy_render_weekly_digest(value, public_origin=ORIGIN)
    assert new == old


def envelope(content, *, testing, template, weekly=False):
    """Route compiled content through the shared Admin report envelope."""
    values = arguments(testing=testing)
    if weekly:
        values["identity"] = replace(values["identity"], purpose="weekly_digest")
    values.update(content=content, template=template)
    result = render_digest_envelope(**values)
    return result.subject, result.html, result.text


TEMPLATES = {
    "default subject, no intro": DigestTemplate(),
    "earlier default intro": DigestTemplate(
        "Report", "<p>Here is yesterday’s report.</p>", "Here is yesterday’s report."
    ),
    "edited intro": DigestTemplate(
        "{{ campaign_name }} digest",
        "<p>Here is yesterday’s report, team.</p>",
        "Here is yesterday’s report, team.",
    ),
}


@pytest.mark.parametrize("testing", [False, True])
@pytest.mark.parametrize("template", TEMPLATES)
def test_sent_daily_and_weekly_mail_is_byte_identical(testing, template, renderer):
    """Subject, intro and Testing heading wrap identical bodies identically."""
    authored = TEMPLATES[template]
    daily = with_comparison()
    if testing:
        daily = replace(daily, funnel=None, mode="testing")
    new, old = same_daily(daily)
    assert envelope(new, testing=testing, template=authored) == envelope(
        old, testing=testing, template=authored
    )
    for manual in (False, True):
        weekly = replace(
            weekly_document(), manual=manual, mode="testing" if testing else None
        )
        new = render_weekly_digest(weekly, public_origin=ORIGIN)
        old = legacy_render_weekly_digest(weekly, public_origin=ORIGIN)
        assert envelope(
            new, testing=testing, template=authored, weekly=True
        ) == envelope(old, testing=testing, template=authored, weekly=True)


# ---------------------------------------------------------------- registry


def test_the_registry_holds_the_six_spec_blocks():
    """Keys, kinds, fact blocks and placement follow the spec's block sections."""
    assert {
        key: (block.kind, block.fact, block.placement, block.reader_role)
        for key, block in REGISTRY.items()
    } == {
        "additional_information": (LIST, False, BODY, "staff"),
        "campaign_totals": (STATUS, True, BODY, "staff"),
        "response_funnel": (STATUS, False, BODY, "staff"),
        "participation_charts": (STATUS, True, BODY, "staff"),
        "day_by_day": (STATUS, True, BODY, "staff"),
        "compared_with_past_campaigns": (STATUS, True, AFTER_BUTTON, "staff"),
    }
    assert {key: (block.label, block.empty) for key, block in REGISTRY.items()} == {
        "additional_information": (
            "Open additional information requests",
            "No open requests.",
        ),
        "campaign_totals": ("Campaign totals", "No campaign day has ended yet."),
        "response_funnel": ("Response funnel", ""),
        "participation_charts": (
            "Participation charts",
            "No campaign day has ended yet.",
        ),
        "day_by_day": ("Day by day table", "No campaign day has ended yet."),
        "compared_with_past_campaigns": ("Compared with past campaigns", ""),
    }
    for block in REGISTRY.values():
        assert block.help.endswith(".")
    assert REGISTRY["day_by_day"].options == {"days": 5}
    assert set(DAILY_BLOCKS) | set(WEEKLY_BLOCKS) == set(REGISTRY)


@pytest.mark.parametrize(
    "changes", [{"kind": "other"}, {"placement": "top"}, {"reader_role": "admin"}]
)
def test_a_block_outside_the_contract_is_rejected(changes):
    """A declaration with a value outside the contract's closed set fails."""
    with pytest.raises(ValueError, match="block contract"):
        replace(REGISTRY["campaign_totals"], **changes)


def test_a_report_lists_each_block_once_and_only_known_blocks():
    """Duplicates are rejected; an unknown key is not drawn."""
    with pytest.raises(ValueError, match="at most once"):
        report_blocks(("campaign_totals", "campaign_totals"))
    with pytest.raises(KeyError):
        report_blocks(("nonexistent",))


def test_a_report_without_facts_needs_its_saved_copy():
    """The closing button of a report with no fact block links its saved copy."""
    with pytest.raises(ValueError, match="saved copy"):
        compile_report(weekly_document(), WEEKLY_BLOCKS, public_origin=ORIGIN)


def test_the_day_by_day_option_sets_the_days_shown():
    """The "days shown" option overrides the default of five."""
    value = long_document(12)
    report = compile_report(
        value,
        ("day_by_day",),
        public_origin=ORIGIN,
        options={"day_by_day": {"days": 9}},
    )
    assert report.html.count('scope="rowgroup"') == 9
    default = compile_report(value, ("day_by_day",), public_origin=ORIGIN)
    assert default.html.count('scope="rowgroup"') == 5


# ------------------------------------------------- which charts (#1956)

ALL_CHARTS = ["first_submissions", "cumulative_families", "pledges"]


def charts_report(value, charts=None):
    """Compile ``value``'s Participation charts block, with ``charts`` chosen."""
    options = {} if charts is None else {"participation_charts": {"charts": charts}}
    return compile_report(
        value, ("participation_charts",), public_origin=ORIGIN, options=options
    )


@pytest.mark.parametrize("case", ["single day, no mode", "no financial stewardship"])
def test_every_chart_chosen_is_the_default_byte_for_byte(case):
    """Choosing all three charts draws the converted Daily's block exactly."""
    value = DAILY_CASES[case]()
    chosen, default = charts_report(value, ALL_CHARTS), charts_report(value)
    assert chosen.html == default.html
    assert [(i.content_id, i.data) for i in chosen.images] == [
        (i.content_id, i.data) for i in default.images
    ]


def test_a_chosen_chart_draws_only_its_panel_and_figures():
    """The image and the alt text carry only the chosen charts."""
    value = daily_document()
    default = charts_report(value)
    first = charts_report(value, ["first_submissions"])
    assert first.images[0].data != default.images[0].data
    assert "First submissions that day:" in first.html
    assert "Families that have responded:" not in first.html
    assert "Cumulative annual pledges" not in first.html
    pledges = charts_report(value, ["cumulative_families", "pledges"])
    assert "Families that have responded:" in pledges.html
    assert "Cumulative annual pledges (USD):" in pledges.html
    assert "First submissions that day:" not in pledges.html


def test_pledges_alone_without_financial_draws_every_chart():
    """Pledges chosen after financial stewardship was turned off: all charts."""
    value = no_financial()
    alone = charts_report(value, ["pledges"])
    default = charts_report(value)
    assert alone.html == default.html
    assert alone.images[0].data == default.images[0].data
    block = REGISTRY["participation_charts"]
    assert block.capture(value, {"charts": ["pledges"]})["charts"] == [
        "first_submissions",
        "cumulative_families",
    ]
    assert block.capture(daily_document(), {"charts": ["pledges"]})["charts"] == [
        "pledges"
    ]


@pytest.mark.parametrize(
    "charts",
    [
        [],
        ["unknown"],
        ["pledges", "pledges"],
        ["pledges", "first_submissions"],
        "pledges",
        None,
        [1],
    ],
)
def test_the_charts_check_refuses_an_empty_unknown_or_unordered_choice(charts):
    """Only a non-empty list of known charts, each once and in order, passes."""
    with pytest.raises(ValueError, match="at least one known chart"):
        REGISTRY["participation_charts"].check({"charts": charts})


def test_the_charts_check_accepts_every_ordered_subset():
    """Any non-empty ordered subset, and no value at all, is accepted."""
    check = REGISTRY["participation_charts"].check
    check({})
    for mask in range(1, 8):
        check(
            {"charts": [key for bit, key in enumerate(ALL_CHARTS) if mask >> bit & 1]}
        )
    assert REGISTRY["participation_charts"].options == {"charts": ALL_CHARTS}
