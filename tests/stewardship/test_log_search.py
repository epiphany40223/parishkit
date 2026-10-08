"""Database-free System logs search grammar, links and predicates (#536)."""

from parishkit.stewardship.audit.log_descriptions import (
    DESCRIPTIONS,
    OUTCOME_DESCRIPTIONS,
    PREFIXES,
    describe,
)
from parishkit.stewardship.audit.log_rows import (
    LINK_FIELDS,
    LogQuery,
    log_table,
    page_context,
)
from parishkit.stewardship.audit.log_search import (
    SHOWN_VALUE_SQL,
    ShownValueContains,
    described_types,
    detail_condition,
    like_pattern,
    ministry_condition,
    type_condition,
)
from parishkit.stewardship.audit.schemas import MINISTRY_LIST_FIELDS

from .test_log_rows import IDENTIFIER, NOW


def test_search_ministry_and_subject_parse_as_closed_values():
    """Text is trimmed; a Ministry is a canonical DUID; a subject a UUID."""
    query = LogQuery.parse(
        {"text": "  refresh failed ", "ministry": "42", "subject": IDENTIFIER}
    )
    assert (query.text, query.ministry, query.subject) == (
        "refresh failed",
        "42",
        IDENTIFIER,
    )
    assert query.private
    assert LogQuery.parse({"text": "é" * 64, "ministry": "2147483647"}).text
    assert not LogQuery.parse({"text": "x"}).private


def test_a_link_carries_only_what_a_web_address_may():
    """Identifiers and the snapshot never reach a link; the zone goes with days."""
    query = LogQuery.parse(
        {
            "applied": "yes",
            "error": "yes",
            "event": "task_failed",
            "text": "lag",
            "ministry": "42",
            "actor": IDENTIFIER,
            "correlation": IDENTIFIER,
            "campaign": IDENTIFIER,
            "subject": IDENTIFIER,
            "start": "2026-09-01",
            "zone": "America/New_York",
            "through": "2026-09-20T12:00:00.123456+00:00",
            "page": "2",
            "size": "25",
            "sort": "oldest",
        }
    )
    values = query.link_values()
    assert values == {
        "applied": "yes",
        "error": "yes",
        "event": "task_failed",
        "ministry": "42",
        "start": "2026-09-01",
        "zone": "America/New_York",
        "size": "25",
        "sort": "oldest",
    }
    assert set(values) <= LINK_FIELDS
    # Private filters stay in POST state: identifiers and the search text.
    assert not {"actor", "correlation", "campaign", "subject", "text"} & LINK_FIELDS
    assert query.unlinked and LogQuery.parse({"text": "lag"}).unlinked
    assert not LogQuery.parse({"ministry": "42"}).unlinked
    assert not {"through", "page"} & LINK_FIELDS
    # Without days the zone means nothing, so a link leaves it out.
    assert LogQuery.parse({"zone": "America/New_York"}).link_values() == {}


def test_page_context_draws_the_link_and_a_followed_links_zone():
    """The page's own address with its link filters; the zone note only for
    a followed link with days."""
    dated = LogQuery.parse(
        {
            "text": "lag",
            "start": "2026-09-01",
            "zone": "Asia/Tokyo",
            "actor": IDENTIFIER,
        }
    )
    table = log_table(dated, [], through=NOW, action="/admin/system/logs/")
    context = page_context(dated, table, linked=True)
    assert context["link"] == "/admin/system/logs/?start=2026-09-01&zone=Asia%2FTokyo"
    assert context["link_zone"] == "Asia/Tokyo"
    assert page_context(dated, table)["link_zone"] == ""
    plain = LogQuery.parse({})
    table = log_table(plain, [], through=NOW, action="/admin/system/logs/")
    assert page_context(plain, table, linked=True)["link"] == "/admin/system/logs/"


def test_text_matches_types_by_their_explanation():
    """A word of a type's sentence finds that type; a trigger prefix's
    sentence finds its prefix; matching ignores case."""
    event, sentence = next(iter(DESCRIPTIONS.items()))
    word = max(str(sentence).split(), key=len).strip(".,:;()\"'")
    types, _prefixes = described_types(word.upper())
    assert event in types
    prefix, prefix_sentence = PREFIXES[0]
    _types, prefixes = described_types(str(prefix_sentence)[2:20])
    assert prefix in prefixes
    assert described_types("zzzz no sentence says this") == (set(), [])


def test_conditions_name_only_reviewed_columns_and_values():
    """The SQL compares the reader's text with icontains or an escaped ILIKE
    pattern, per shown value of a reviewed field, and the Ministry with JSONB
    containment on every Ministry field."""
    assert "event__icontains" in str(type_condition("lag", type_column="event"))
    # The cheap condition over every value comes before the exact one.
    cheap, exact = detail_condition("lag").children
    assert cheap == ("every_value__icontains", "lag")
    assert isinstance(exact, ShownValueContains) and exact.text == "lag"
    # JSON text escapes a double quote or backslash, so such a search skips
    # the quick prefilter and uses the exact condition alone.
    for text in ('say "yes"', "a\\b"):
        (alone,) = detail_condition(text).children
        assert isinstance(alone, ShownValueContains) and alone.text == text
    # Only whole numbers are shown, so only whole numbers are searched.
    assert "field.value::numeric = trunc(field.value::numeric)" in SHOWN_VALUE_SQL
    assert "@ != @.floor()" in SHOWN_VALUE_SQL
    ministry = str(ministry_condition(42))
    for field in ("ministry_duid", *MINISTRY_LIST_FIELDS):
        assert field in ministry
    # Each value on its own, only reviewed fields, only what the page shows.
    assert "field.key = ANY(%s)" in SHOWN_VALUE_SQL
    assert "length(field.value #>> '{{}}') <= %s" in SHOWN_VALUE_SQL
    assert "'object'" not in SHOWN_VALUE_SQL
    assert like_pattern("50%_a\\b") == "%50\\%\\_a\\\\b%"


def test_outcome_sentences_follow_the_shown_explanation():
    """An operational type whose sentence depends on its outcome matches by
    the sentence ``describe`` shows for each entry's outcome (#536)."""
    (event, outcome), sentence = next(iter(OUTCOME_DESCRIPTIONS.items()))
    special = " ".join(str(sentence).split()[:3])
    plain = " ".join(str(describe(event)).split()[:3])
    assert special.casefold() not in str(describe(event)).casefold()
    found = str(type_condition(special, type_column="event", outcomes=True))
    assert f"('event', '{event}')" in found and f"'outcome': '{outcome}'" in found
    assert "NOT" not in found
    other = str(type_condition(plain, type_column="event", outcomes=True))
    assert f"(NOT (AND: ('context__contains', {{'outcome': '{outcome}'}})))" in other
    # Audit entries are explained by their type alone.
    assert "outcome" not in str(type_condition(special, type_column="event_type"))
