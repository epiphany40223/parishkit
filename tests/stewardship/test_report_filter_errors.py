"""Report filters refused at their field, not on an error page (#1332)."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from django.http import HttpResponse, QueryDict
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory

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
from parishkit.stewardship.reports import ministry_export_views
from parishkit.stewardship.reports.directories import (
    CODE_MESSAGE,
    DIRECTORY_SORTING,
    DirectoryQuery,
)
from parishkit.stewardship.reports.directory_views import (
    FIELD_IDS as DIRECTORY_IDS,
)
from parishkit.stewardship.reports.directory_views import (
    FIELD_LINES as DIRECTORY_LINES,
)
from parishkit.stewardship.reports.financial import (
    DATES,
    FIELD_MESSAGES,
    FinancialQuery,
)
from parishkit.stewardship.reports.financial_views import FIELD_IDS, FIELD_LINES
from parishkit.stewardship.reports.information import (
    FIELD_MESSAGES as INFORMATION_MESSAGES,
)
from parishkit.stewardship.reports.information import InformationQuery
from parishkit.stewardship.reports.ministry_export_views import (
    PACKET_MESSAGE,
    TOO_MANY,
    TOO_MANY_FIX,
    TooManyMinistries,
    packet_selection,
)
from parishkit.stewardship.reports.ministry_exports import MAX_PACKET_MINISTRIES
from parishkit.stewardship.reports.ministry_followup_views import packet_context
from parishkit.stewardship.web.dates import UnknownZone
from parishkit.stewardship.web.filter_errors import (
    FilterError,
    error_context,
    line_sizes,
    refused,
)
from parishkit.stewardship.web.tables import report_table

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
    tag, and its range's reserved line is drawn once. A response date
    passes its zone note through the mark tag (#558); a From date's hint is
    on its cell, since the zone check replaces an input's own hint, and a
    Through date's order hint, which the zone check restores, is its
    message too.
    """
    assert set(FIELD_IDS) == set(FIELD_MESSAGES) == set(FIELD_LINES)
    for field, element in FIELD_IDS.items():
        cell = re.search(
            r'<div(?: class="money-input")?(?: data-missing-hint="[^"]*")?>'
            rf'<label for="{element}">.*?</div>',
            TEMPLATE,
        ).group(0)
        tag = re.search(rf'<input id="{element}" [^>]*>', cell).group(0)
        assert f'name="{field}"' in tag and "aria-describedby" not in tag
        described = ' "financial-zone-help"' if field in DATES else ""
        assert f'{{% filter_mark filter_error "{field}"{described} %}}' in tag
        hints = re.findall(
            r"data-(?:missing|order)-hint=\"{% translate '([^']*)' %}\"", tag
        ) or re.findall(r"data-missing-hint=\"{% translate '([^']*)' %}\"", cell)
        ends = {"first_end", "latest_end"}
        assert hints == [str(FIELD_MESSAGES[field])] * (2 if field in ends else 1)
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


# The Additional information selection's date filters (#1332, second
# slice). Its page is the review worklist since #1685, which refuses a bad
# date with its error page, as Census changes does; the selection still
# names each date's field for an export or a command.
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


# Ministry requests' Follow-up packet limit (#1332, fourth slice): a form,
# not a filter, refused at its field with the same pieces.
PACKET = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship/ministry-packet.html"
).read_text()
CHOICES = [{"duid": 4, "name": "Choir"}, {"duid": 9, "name": "Food pantry"}]


def packet_form(count):
    """The packet form's POST body with ``count`` Ministries ticked."""
    values = QueryDict(mutable=True)
    values.update({"format": "pdf", "browser_timezone": "UTC", "request_key": "k"})
    values.setlist("ministries", [str(duid) for duid in range(1, count + 1)])
    return values


def boxes(html):
    """The packet's checkbox tags, in order."""
    return re.findall(r'<input type="checkbox" id="ministry-packet-[^>]*>', html)


def test_packet_limit_keeps_the_selection_sent():
    """Past the limit the refusal carries the selection, and is a ValueError."""
    with pytest.raises(TooManyMinistries) as caught:
        packet_selection(packet_form(MAX_PACKET_MINISTRIES + 1))
    assert caught.value.chosen == tuple(
        str(duid) for duid in range(1, MAX_PACKET_MINISTRIES + 2)
    )
    assert isinstance(caught.value, ValueError)
    assert str(PACKET_MESSAGE) == f"{TOO_MANY} {TOO_MANY_FIX}"


def test_packet_context_marks_every_box_and_links_the_first():
    """The summary links to the first box; the row is sized to the message."""
    error = FilterError("ministries", PACKET_MESSAGE, ("9", "77"))
    context = packet_context(error, CHOICES)
    assert context["filter_error"] == {
        "fields": ("ministries",),
        "line": "ministry-packet",
        "message": PACKET_MESSAGE,
    }
    assert context["errors"] == [
        {"field_id": "ministry-packet-4", "message": PACKET_MESSAGE}
    ]
    assert context["packet_ticked"] == {"9", "77"}
    assert context["packet_sizes"] == {"ministry-packet": PACKET_MESSAGE}
    # A reader who may include no Ministry has no box: the heading.
    assert packet_context(error, [])["errors"][0]["field_id"] == (
        "ministry-packet-heading"
    )


def test_refused_packet_keeps_offered_ticks_with_the_message_in_the_hint_row():
    """Only offered boxes are ticked again; each is marked and described by
    the one message line, which shares the hint row with the gate's hint."""
    error = FilterError("ministries", "Tick <b>fewer</b>.", ("9", "77", '"><x'))
    html = render_to_string(
        "stewardship/ministry-packet.html",
        {"packet_choices": CHOICES, "packet_limit": 200, "mutable": True}
        | packet_context(error, CHOICES),
    )
    choir, pantry = boxes(html)
    assert "checked" not in choir and " checked" in pantry
    for tag in (choir, pantry):
        assert 'aria-invalid="true"' in tag
        assert 'aria-describedby="ministry-packet_error"' in tag
    assert "77" not in html and "&quot;&gt;&lt;x" not in html
    row = html[html.index('<div class="hint-row-messages">') :]
    assert row.startswith(
        '<div class="hint-row-messages"><p class="help" id="ministry-packet-hint"'
        ' data-complete-hint="reserve"></p><div class="field-error-line">'
    )
    assert html.count("Tick &lt;b&gt;fewer&lt;/b&gt;.") == 2  # message and size
    assert '<ul class="errorlist" id="ministry-packet_error"><li>' in html


def test_unrefused_packet_draws_no_mark_and_an_empty_line():
    """An ordinary page has no mark and a line that takes no room."""
    html = render_to_string(
        "stewardship/ministry-packet.html",
        {"packet_choices": CHOICES, "packet_limit": 200, "mutable": True},
    )
    for tag in boxes(html):
        assert "aria-invalid" not in tag and "aria-describedby" not in tag
        assert "checked" not in tag
    assert (
        '<div class="field-error-line"><span class="field-error-size"'
        ' aria-hidden="true"></span><ul class="errorlist" id="ministry-packet_error"'
        " hidden><li></li></ul></div></div></form>"
    ) in html


class Stub:
    """A stand-in runtime and principal for the views below."""

    store = None
    identity = UUID(int=5)


@pytest.mark.parametrize("accept", ["text/html", "application/json"])
def test_packet_limit_draws_the_queue_for_a_person_json_for_a_script(
    monkeypatch, accept
):
    """A person gets Ministry requests again; a script, the typed refusal."""
    drawn = []
    monkeypatch.setattr(ministry_export_views, "runtime", Stub)
    monkeypatch.setattr(ministry_export_views, "_principal", lambda *_: Stub)
    monkeypatch.setattr(
        ministry_export_views,
        "queue_response",
        lambda request, campaign, *, packet: (
            drawn.append((campaign, packet)) or HttpResponse("queue", status=400)
        ),
    )
    campaign = UUID(int=7)
    body = dict(packet_form(MAX_PACKET_MINISTRIES + 1).lists())
    request = RequestFactory().post("/packet", body, HTTP_ACCEPT=accept)
    response = ministry_export_views.create_packet.__wrapped__(request, campaign)
    assert response.status_code == 400
    if accept == "text/html":
        ((shown, error),) = drawn
        assert shown == campaign and error.field == "ministries"
        assert error.message == PACKET_MESSAGE and len(error.query) == 201
    else:
        assert not drawn
        assert "at most 200" in json.loads(response.content)["refusal"]["message"]


DIRECTORY = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship/directory.html"
).read_text()


@pytest.mark.parametrize("code", ["ABC", "ABCD-EFG1", "ÀBCDEFGH", "A" * 129])
def test_directory_code_rule_names_its_field(code):
    """A malformed Exact Family code is refused at its field, with the query
    as sent (its closed choices all accepted) and the field hint's words."""
    with pytest.raises(FilterError) as caught:
        DirectoryQuery.parse(
            {
                "exact_code": code,
                "search": "Example",
                "sort": "duid_desc",
                "phone": "yes",
            }
        )
    error = caught.value
    assert error.fields == ("exact_code",) and error.message == CODE_MESSAGE
    assert error.query.exact_code == code and error.query.search == "Example"
    assert error.query.sort == "duid_desc" and error.query.phone == "yes"
    # Still a ValueError: the export and pk-admin refuse it as before.
    assert isinstance(error, ValueError)


@pytest.mark.parametrize(
    "values",
    [
        {"exact_code": "ABC", "sort": "random"},
        {"exact_code": "ABC", "phone": "maybe"},
        {"exact_code": "ABC", "extra": "x"},
        {"exact_code": "ABC", "search": "x" * 201},
        {"exact_code": "ABC", "page": "0"},
    ],
)
def test_directory_structural_refusals_keep_the_error_page(values):
    """A refusal no field can show stays a plain ValueError, even with a
    malformed code beside it: the structural rules are checked first."""
    with pytest.raises(ValueError) as caught:
        DirectoryQuery.parse(values)
    assert not isinstance(caught.value, FilterError)


def test_directory_message_is_the_hint_and_the_field_is_wired():
    """The server's words are the code field's hint; the field carries the
    mark tag and the gate's hint row draws the one message line."""
    assert set(DIRECTORY_IDS) == set(DIRECTORY_LINES) == {"exact_code"}
    tag = re.search(r'<input id="exact-code" [^>]*>', DIRECTORY).group(0)
    assert f"data-missing-hint=\"{{% translate '{CODE_MESSAGE}' %}}\"" in tag
    assert '{% filter_mark filter_error "exact_code" %}' in tag
    assert "aria-describedby" not in tag
    assert DIRECTORY.count('{% filter_line filter_error "directory-filters" ') == 1
    assert (
        '<div class="hint-row-messages"><p class="help" id="directory-filter-hint"'
        ' data-complete-hint="reserve"></p>{% filter_line filter_error'
        ' "directory-filters" filter_sizes %}</div></form>'
    ) in DIRECTORY


def directory_html(**context):
    """The directory page for an empty selection, with ``context`` added."""
    return render_to_string(
        "stewardship/directory.html",
        {
            "campaign_id": UUID(int=80),
            "metadata": {"source_generation": 1},
            "total": 0,
            "table": report_table(
                [],
                number=1,
                size=50,
                total=0,
                carry=(),
                sorting=DIRECTORY_SORTING,
                sort="name",
                action="/admin/directory/",
                sizes=(50,),
            ),
            "query": DirectoryQuery(),
            "query_fields": DirectoryQuery().form_values(),
        }
        | context,
    )


def test_refused_directory_shows_what_was_sent_with_the_mark():
    """The form shows the filters sent; the results' fields, none.

    The code field is marked and described by the hint row's message line,
    which is sized to it; the panel counts only the mailing-columns choice
    kept, and the
    export carries none of the values sent.
    """
    sent = DirectoryQuery(search="Adams", exact_code='AB"<x', phone="yes")
    error = FilterError("exact_code", CODE_MESSAGE, sent)
    html = directory_html(
        **error_context(error, DIRECTORY_IDS, DIRECTORY_LINES),
        filter_query=sent,
        mailing=True,
        filter_sizes={"directory-filters": CODE_MESSAGE},
    )
    form = html[html.index('<form id="table-filters"') : html.index("</form>")]
    assert 'value="Adams"' in form and 'value="AB&quot;&lt;x"' in form
    assert '<option value="yes" selected>' in form
    assert 'name="mailing" value="yes" checked' in form
    tag = re.search(r'<input id="exact-code" [^>]*>', form).group(0)
    assert 'aria-invalid="true" aria-describedby="directory-filters_error"' in tag
    message = str(CODE_MESSAGE)
    assert (
        f'<span class="field-error-size" aria-hidden="true">{message}</span>'
        f'<ul class="errorlist" id="directory-filters_error"><li>{message}</li></ul>'
    ) in form
    assert f'<a href="#exact-code">{message}</a>' in html
    # Only the mailing-columns choice, kept, is counted.
    assert ">1 applied<" in html and "data-filter-panel open" in html
    export = html[html.index('<form id="table-export"') :]
    assert "Adams" not in export and "AB&quot;" not in export


def test_unrefused_directory_draws_no_mark_and_an_empty_line():
    """An ordinary page has no mark and a hidden line that takes no room."""
    html = directory_html(query=DirectoryQuery(exact_code="ABCDEFGH"), mailing=True)
    tag = re.search(r'<input id="exact-code" [^>]*>', html).group(0)
    assert 'value="ABCDEFGH"' in tag
    assert "aria-invalid" not in tag and "aria-describedby" not in tag
    assert 'name="mailing" value="yes" checked' in html
    assert (
        '<div class="field-error-line"><span class="field-error-size"'
        ' aria-hidden="true"></span><ul class="errorlist" id="directory-filters_error"'
        " hidden><li></li></ul></div></div></form>"
    ) in html
