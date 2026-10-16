"""Report filters refused at their field, not on an error page (#1332)."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.http import HttpResponse
from django.template import Context, Template

from parishkit.stewardship.audit.log_rows import (
    AUDIT_ONLY_MESSAGE,
    LogQuery,
    NothingShown,
    log_table,
    page_context,
)
from parishkit.stewardship.audit.log_rows import FIELD_MESSAGES as LOG_MESSAGES
from parishkit.stewardship.audit.log_views import FIELD_IDS as LOG_IDS
from parishkit.stewardship.audit.log_views import FIELD_LINES as LOG_LINES
from parishkit.stewardship.audit.log_views import field_ids, refusal_sizes
from parishkit.stewardship.reports.financial import FIELD_MESSAGES, FinancialQuery
from parishkit.stewardship.reports.financial_views import FIELD_IDS, FIELD_LINES
from parishkit.stewardship.reports.information import (
    FIELD_MESSAGES as INFORMATION_MESSAGES,
)
from parishkit.stewardship.reports.information import InformationQuery
from parishkit.stewardship.reports.information_views import (
    FIELD_IDS as INFORMATION_IDS,
)
from parishkit.stewardship.reports.information_views import (
    FIELD_LINES as INFORMATION_LINES,
)
from parishkit.stewardship.web.dates import UnknownZone
from parishkit.stewardship.web.filter_errors import (
    FilterError,
    error_context,
    line_sizes,
    refused,
)

TEMPLATE = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship/financial-report.html"
).read_text()


def render(source, **context):
    """Render a template that loads the stewardship tag library."""
    return Template("{% load stewardship %}" + source).render(Context(context))


@pytest.mark.parametrize(
    ("values", "fields"),
    [
        ({"first_start": "2026-02-30"}, ("first_start",)),
        ({"first_end": "soon"}, ("first_end",)),
        ({"latest_start": "2026-1-5"}, ("latest_start",)),
        ({"latest_end": "2026-13-01"}, ("latest_end",)),
        (
            {"first_start": "2026-09-02", "first_end": "2026-09-01"},
            ("first_end", "first_start"),
        ),
        (
            {"latest_start": "2026-09-02", "latest_end": "2026-09-01"},
            ("latest_end", "latest_start"),
        ),
        ({"pledge_min": "1,234"}, ("pledge_min",)),
        ({"pledge_max": "12.5"}, ("pledge_max",)),
        ({"pledge_min": "9", "pledge_max": "1"}, ("pledge_max", "pledge_min")),
    ],
)
def test_financial_field_rules_name_their_fields(values, fields):
    """Each rule names its field (a range's order both ends), words and query."""
    with pytest.raises(FilterError) as caught:
        FinancialQuery.parse(values | {"search": "Example", "sort": "pledge_desc"})
    error = caught.value
    assert error.fields == fields and error.field == fields[0]
    assert error.message == FIELD_MESSAGES[error.field]
    # The query as sent, for the form; its closed choices were all accepted.
    assert error.query.search == "Example" and error.query.sort == "pledge_desc"
    assert getattr(error.query, error.field) == values[error.field]
    # Still a ValueError: exports and pk-admin refuse it as before.
    assert isinstance(error, ValueError)


@pytest.mark.parametrize(
    "values",
    [
        {"sort": "random"},
        {"amount": "all"},
        {"share": "no-such-option"},
        {"extra": "x"},
        {"search": "x" * 201},
        {"page": "0"},
    ],
)
def test_financial_structural_refusals_keep_the_error_page(values):
    """A refusal no field can show stays a plain ValueError."""
    with pytest.raises(ValueError) as caught:
        FinancialQuery.parse(values)
    assert not isinstance(caught.value, FilterError)


def test_financial_messages_are_the_page_hints_and_fields_exist():
    """The server's words are the hints the browser shows first, at real ids.

    Every refusable field has a hint (data-missing-hint) that says the same
    as the server, so the two never disagree, its element carries the mark
    tag, and its range's reserved line is drawn once.
    """
    assert set(FIELD_IDS) == set(FIELD_MESSAGES) == set(FIELD_LINES)
    for field, element in FIELD_IDS.items():
        tag = re.search(rf'<input id="{element}" [^>]*>', TEMPLATE).group(0)
        assert f'name="{field}"' in tag
        assert f'{{% filter_mark filter_error "{field}" %}}' in tag
        hint = re.search(r"data-missing-hint=\"{% translate '([^']*)' %}\"", tag)
        assert hint.group(1) == str(FIELD_MESSAGES[field])
    for line in set(FIELD_LINES.values()):
        assert TEMPLATE.count(f'{{% filter_line filter_error "{line}" ') == 1


def test_mark_merges_with_the_field_s_own_descriptions():
    """One aria-describedby: the field's own ids, then its message line."""
    error = FilterError("first_end", "Fix it.", FinancialQuery(), pair="first_start")
    context = error_context(error, FIELD_IDS, FIELD_LINES)
    source = (
        '<input id="first-start"{% filter_mark filter_error "first_start" %}>'
        '<input id="first-end"'
        '{% filter_mark filter_error "first_end" "zone-help other-help" %}>'
        '<input id="pledge-min"{% filter_mark filter_error "pledge_min" "help" %}>'
        '<input id="pledge-max"{% filter_mark filter_error "pledge_max" %}>'
    )
    assert render(source, **context) == (
        '<input id="first-start" aria-invalid="true"'
        ' aria-describedby="first-response-dates_error">'
        '<input id="first-end" aria-invalid="true"'
        ' aria-describedby="zone-help other-help first-response-dates_error">'
        '<input id="pledge-min" aria-describedby="help">'
        '<input id="pledge-max">'
    )
    # No refusal: only the field's own ids.
    assert render(source) == (
        '<input id="first-start">'
        '<input id="first-end" aria-describedby="zone-help other-help">'
        '<input id="pledge-min" aria-describedby="help">'
        '<input id="pledge-max">'
    )
    # The base template's summary links to the field refused, the end.
    assert context["errors"] == [{"field_id": "first-end", "message": "Fix it."}]


def test_line_is_reserved_and_shows_only_its_own_refusal():
    """The line is always drawn at its longest message's size, hidden when idle."""
    sizes = line_sizes(FIELD_MESSAGES, FIELD_LINES)
    assert sizes["pledge-range"] == str(FIELD_MESSAGES["pledge_min"])
    error = FilterError("pledge_max", "Enter <b>less</b>.", FinancialQuery())
    context = error_context(error, FIELD_IDS, FIELD_LINES) | {"sizes": sizes}
    source = '{% filter_line filter_error "pledge-range" sizes %}'
    size = (
        '<div class="field-error-line"><span class="field-error-size"'
        ' aria-hidden="true">' + sizes["pledge-range"] + "</span>"
    )
    assert render(source, **context) == (
        size + '<ul class="errorlist" id="pledge-range_error">'
        "<li>Enter &lt;b&gt;less&lt;/b&gt;.</li></ul></div>"
    )
    idle = (
        size
        + '<ul class="errorlist" id="pledge-range_error" hidden><li></li></ul></div>'
    )
    assert render(source, sizes=sizes) == idle
    # Another range's refusal leaves this line idle.
    other = FilterError("first_end", "Fix it.", FinancialQuery())
    assert (
        render(source, sizes=sizes, **error_context(other, FIELD_IDS, FIELD_LINES))
        == idle
    )


def test_panel_opens_for_a_refused_filter_with_none_applied():
    """An error inside the Filters panel is never hidden behind it."""
    source = "{% filterpanel query %}<p>x</p>{% endfilterpanel %}"
    assert '<details class="filter-panel" data-filter-panel>' in render(
        source, query=FinancialQuery()
    )
    error = FilterError("first_end", "Enter a date.", FinancialQuery())
    html = render(
        source, query=FinancialQuery(), **error_context(error, FIELD_IDS, FIELD_LINES)
    )
    assert '<details class="filter-panel" data-filter-panel open>' in html
    # The count is of the filters the results use: none.
    assert "none applied" in html


def test_refused_answers_400_kept_and_logged_as_invalid():
    """The page drawn again is the view's own 400, with its refusal code."""
    response = refused(HttpResponse("page"))
    assert response.status_code == 400 and response.stewardship_safe_error is True
    assert response.stewardship_codes == ("invalid",)
    assert not hasattr(response, "stewardship_errors")


# The Additional information queue's date filters (#1332, second slice).
INFORMATION = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship/information.html"
).read_text()
ZONE = "America/New_York"


@pytest.mark.parametrize(
    ("values", "fields"),
    [
        ({"start": "2026-02-30"}, ("start",)),
        ({"start": "20260101"}, ("start",)),
        ({"end": "not-a-date"}, ("end",)),
        ({"start": "2026-02-01", "end": "2026-01-01"}, ("end", "start")),
        # A malformed date is refused at its field even without a zone.
        ({"start": "soon", "zone": ""}, ("start",)),
    ],
)
def test_information_date_rules_name_their_fields(values, fields):
    """Each date rule names its field (out of order, both), words and query."""
    with pytest.raises(FilterError) as caught:
        InformationQuery.parse(
            {"search": "Example", "sort": "oldest", "zone": ZONE} | values
        )
    error = caught.value
    assert error.fields == fields and error.field == fields[0]
    assert error.message == INFORMATION_MESSAGES[error.field]
    assert error.query.search == "Example" and error.query.sort == "oldest"
    assert getattr(error.query, error.field) == values[error.field]
    assert isinstance(error, ValueError)


@pytest.mark.parametrize(
    "values",
    [
        {"sort": "notes"},
        {"disposition": "deleted"},
        {"email": "private@example.org"},
        {"search": "x" * 201},
        {"page": "0"},
    ],
)
def test_information_structural_refusals_keep_the_error_page(values):
    """A refusal no field can show stays a plain ValueError."""
    with pytest.raises(ValueError) as caught:
        InformationQuery.parse(values)
    assert not isinstance(caught.value, FilterError)


def test_information_zone_refusal_keeps_its_own_page():
    """Whole, ordered dates without a known zone are no field's to fix."""
    with pytest.raises(UnknownZone) as caught:
        InformationQuery.parse({"start": "2026-01-01", "zone": ""})
    assert not isinstance(caught.value, FilterError)


def test_information_messages_are_the_page_hints_and_fields_exist():
    """The server's words are the hints the browser shows first, at real ids.

    The start's hint is on its cell (the zone check replaces an input's
    own hint); the end's own hint and its order hint, which the zone check
    restores, are both the end's message. Each field passes its zone note
    through the mark tag, and the dates' line is drawn once.
    """
    assert set(INFORMATION_IDS) == set(INFORMATION_MESSAGES) == set(INFORMATION_LINES)
    hints = {}
    for field, element in INFORMATION_IDS.items():
        cell = re.search(
            rf'<div(?: data-missing-hint="[^"]*")?><label for="{element}">.*?</div>',
            INFORMATION,
        ).group(0)
        tag = re.search(rf'<input id="{element}" [^>]*>', cell).group(0)
        assert f'name="{field}"' in tag and "aria-describedby" not in tag
        assert (
            f'{{% filter_mark filter_error "{field}" "information-zone-help" %}}' in tag
        )
        hints[field] = re.findall(
            r"data-(?:missing|order)-hint=\"{% translate '([^']*)' %}\"", tag
        ) or re.findall(r"data-missing-hint=\"{% translate '([^']*)' %}\"", cell)
    assert hints["start"] == [str(INFORMATION_MESSAGES["start"])]
    assert hints["end"] == [str(INFORMATION_MESSAGES["end"])] * 2
    assert INFORMATION.count('{% filter_line filter_error "submitted-dates" ') == 1
    assert "{% filterpanel applied|default:query %}" in INFORMATION


def test_information_mark_keeps_the_zone_note():
    """Both dates keep their zone note, then the line, in one attribute."""
    error = FilterError("end", "Fix it.", InformationQuery(), pair="start")
    context = error_context(error, INFORMATION_IDS, INFORMATION_LINES)
    source = "".join(
        f'<input id="{field}"'
        f'{{% filter_mark filter_error "{field}" "information-zone-help" %}}>'
        for field in ("start", "end")
    )
    marked = (
        ' aria-invalid="true"'
        ' aria-describedby="information-zone-help submitted-dates_error">'
    )
    assert render(source, **context) == (
        '<input id="start"' + marked + '<input id="end"' + marked
    )
    assert render(source) == (
        '<input id="start" aria-describedby="information-zone-help">'
        '<input id="end" aria-describedby="information-zone-help">'
    )
    assert context["errors"] == [{"field_id": "end", "message": "Fix it."}]


# System logs (#1332, third slice): nothing ticked, the identifiers and the
# dates. Its filter form is a filter bar, not a Filters panel.
LOGS = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship/logs.html"
).read_text()
UUID_TEXT = "0000000a-0000-0000-0000-000000000042"


@pytest.mark.parametrize(
    ("values", "fields"),
    [
        ({"applied": "yes"}, ("show",)),
        # A retired Source choice that leaves nothing ticked, as before.
        ({"applied": "yes", "source": "operational"}, ("show",)),
        ({"actor": "not-a-uuid"}, ("actor",)),
        ({"correlation": UUID_TEXT.upper()}, ("correlation",)),
        ({"campaign": UUID_TEXT[:-1]}, ("campaign",)),
        ({"subject": "{" + UUID_TEXT + "}"}, ("subject",)),
        ({"start": "2026-02-30"}, ("start",)),
        ({"end": "20260101"}, ("end",)),
        ({"start": "2026-02-02", "end": "2026-02-01"}, ("end", "start")),
        # A malformed date is refused at its field even without a zone.
        ({"start": "soon", "zone": ""}, ("start",)),
        # In the form's order: the Show choices come first.
        ({"applied": "yes", "actor": "x", "start": "x"}, ("show",)),
        ({"actor": "x", "start": "x"}, ("actor",)),
    ],
)
def test_log_field_rules_name_their_fields(values, fields):
    """Each field rule names its field (out of order, both), words and query."""
    with pytest.raises(FilterError) as caught:
        LogQuery.parse(
            {"text": "lag", "sort": "oldest", "size": "50", "zone": ZONE} | values
        )
    error = caught.value
    assert error.fields == fields and error.field == fields[0]
    assert error.message == LOG_MESSAGES[error.field]
    assert error.query.text == "lag" and error.query.sort == "oldest"
    assert error.query.size == "50"
    if error.field != "show":
        assert getattr(error.query, error.field) == values[error.field]
    # The download and pk-admin still refuse it as before.
    assert isinstance(error, ValueError)
    assert isinstance(error, NothingShown) == (error.field == "show")


@pytest.mark.parametrize(
    "values",
    [
        {"sort": "created_at"},
        {"size": "all"},
        {"page": "0x1"},
        {"through": "2026-09-20T12:00:00"},
        {"event": "drop table"},
        {"text": "admin@example.org"},
        {"ministry": "042"},
        # A whole day outside the log's years: no hint names the range.
        {"start": "2019-12-31", "zone": ZONE},
        {"end": "9999-12-31", "zone": ZONE},
        # A structural refusal wins over a field's, so a refused page's
        # sort and size are always ones the table accepts.
        {"applied": "yes", "sort": "created_at"},
        {"actor": "x", "size": "all"},
        {"start": "x", "end": "2019-01-01", "zone": ZONE},
    ],
)
def test_log_structural_refusals_keep_the_error_page(values):
    """A refusal no field can show stays a plain ValueError."""
    with pytest.raises(ValueError) as caught:
        LogQuery.parse(values)
    assert not isinstance(caught.value, FilterError)


def test_log_zone_refusal_keeps_its_own_page():
    """Whole, ordered dates without a known zone are no field's to fix."""
    with pytest.raises(UnknownZone) as caught:
        LogQuery.parse({"start": "2026-01-01", "zone": ""})
    assert not isinstance(caught.value, FilterError)


def test_log_messages_are_the_page_hints_and_fields_exist():
    """The server's words are the hints the browser shows first, at real ids.

    Each field's hint is on its cell (the Show choices' and Audit record's
    from the server's own strings, page_context), each field passes its own
    description through the mark tag, every Show box is marked, and the one
    message line is drawn once, in the gate's hint row (#1474).
    """
    assert set(LOG_IDS) == set(LOG_MESSAGES) == set(LOG_LINES)
    show = re.search(r"<fieldset [^>]*>", LOGS).group(0)
    assert 'data-missing-hint="{{ show_hint }}"' in show
    assert (
        '<label{% if key == "audit" %} data-missing-hint="{{ audit_only_hint }}"'
        in (LOGS)
    )
    table = log_table(
        LogQuery(), [], through=datetime(2026, 9, 1, tzinfo=UTC), action="/"
    )
    hints = page_context(LogQuery(), table)
    assert hints["show_hint"] == LOG_MESSAGES["show"]
    assert hints["audit_only_hint"] == AUDIT_ONLY_MESSAGE
    assert 'id="log-show-{{ key }}"' in LOGS
    assert LOGS.count('{% filter_mark filter_error "show" %}') == 1  # In the loop.
    described = {"start": "log-zone-help", "end": "log-zone-help"}
    described["subject"] = "log-subject-help"
    for field in set(LOG_IDS) - {"show"}:
        element = LOG_IDS[field]
        cell = re.search(
            rf'<div[^>]*data-missing-hint="{{% translate \'([^\']*)\' %}}"><'
            rf'(?:div class="label-row"><)?label for="{element}".*?</div>',
            LOGS,
        )
        assert cell.group(1) == str(LOG_MESSAGES[field]), field
        tag = re.search(rf'<input id="{element}" [^>]*>', cell.group(0)).group(0)
        assert f'name="{field}"' in tag and "aria-describedby" not in tag
        extra = f' "{described[field]}"' if field in described else ""
        assert f'{{% filter_mark filter_error "{field}"{extra} %}}' in tag, field
    assert set(LOG_LINES.values()) == {"log-filters"}
    assert LOGS.count("{% filter_line ") == 1
    assert (
        '<div class="log-filter-messages"><p class="help" id="log-filter-hint"'
        ' data-complete-hint="reserve"></p>'
        '{% filter_line filter_error "log-filters" filter_sizes %}</div>'
    ) in LOGS
    assert field_ids(LogQuery()) == LOG_IDS
    audit_only = LogQuery(activity="sign_in")
    assert field_ids(audit_only) == LOG_IDS | {"show": "log-show-audit"}


def test_log_marks_keep_their_descriptions():
    """A marked date keeps its zone note and the subject its help, then the
    line, in one attribute; an unmarked one keeps only its own."""
    error = FilterError("end", "Fix it.", LogQuery(), pair="start")
    context = error_context(error, LOG_IDS, LOG_LINES)
    source = (
        '<input id="log-start"{% filter_mark filter_error "start" "log-zone-help" %}>'
        '<input id="log-subject"'
        '{% filter_mark filter_error "subject" "log-subject-help" %}>'
    )
    assert render(source, **context) == (
        '<input id="log-start" aria-invalid="true"'
        ' aria-describedby="log-zone-help log-filters_error">'
        '<input id="log-subject" aria-describedby="log-subject-help">'
    )
    assert context["errors"] == [{"field_id": "log-end", "message": "Fix it."}]
    shown = NothingShown("show", LOG_MESSAGES["show"], LogQuery(applied="yes"))
    assert error_context(shown, LOG_IDS, LOG_LINES)["filter_error"] == {
        "fields": ("show",),
        "line": "log-filters",
        "message": LOG_MESSAGES["show"],
    }


def test_log_page_context_shows_what_was_sent():
    """The filter form shows the query sent; the rest uses the one applied."""
    sent = LogQuery(applied="yes", activity="sign_in", ministry="7")
    applied = LogQuery(sort="oldest")
    table = log_table(applied, [], through=datetime(2026, 9, 1, tzinfo=UTC), action="/")
    context = page_context(applied, table, sent=sent)
    assert context["query"] is sent and context["applied"] is applied
    assert not any(chosen for _key, _label, chosen in context["levels"])
    assert context["ministry_unlisted"] is True
    assert context["query_fields"] == applied.form_values()
    plain = page_context(applied, table)
    assert plain["query"] is plain["applied"] is applied


def test_log_nothing_ticked_under_an_audit_only_activity_says_tick_audit():
    """Nothing ticked under an audit-only Activity is refused in Audit
    record's own words, the hint the gate shows; any other Activity keeps
    the Show choices' words. The hint row is sized to the message shown."""
    for activity, message in (
        ("sign_in", AUDIT_ONLY_MESSAGE),
        ("parishsoft", LOG_MESSAGES["show"]),
        ("", LOG_MESSAGES["show"]),
    ):
        with pytest.raises(NothingShown) as caught:
            LogQuery.parse({"applied": "yes", "activity": activity})
        assert caught.value.message == message
        assert refusal_sizes(caught.value) == {"log-filters": message}
