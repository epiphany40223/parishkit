"""Text, Ministry and subject filters for the System logs read path (#536).

Kept apart from the read path itself so the screen, its export and any other
reader of ``LogQuery`` narrow both logs the same way with one call
(``narrowed``).

Text search matches three things, case-insensitively: the stored type, the
entry's plain-language explanation as the page shows it, and the recorded
detail values the page shows. Key names are never searched, and only fields
in ``DETAIL_FIELDS`` are, each value on its own (no match across two values
or through JSON quoting). Both logs' contexts hold only reviewed, closed
fields, and a value the page would not show (a string longer than
``DETAIL_LIMIT``, an object, a list holding anything but numbers) never
matches, so searching cannot reveal what the page hides. The search text
travels only in POST bodies, never in a link (``log_rows.LINK_FIELDS``).

A cheap condition over the text of every stored value runs first; the
exact per-value condition runs only on the rows it passes. Trigram indexes
(#765, frozen migration file 0116) hold exactly the ``UPPER(...)``
expressions Django's ``icontains`` compares for the cheap condition and the
stored type, and pattern btrees serve the other type arms (the listed types
and the trigger prefixes), so PostgreSQL combines every arm of the text OR
in one bitmap scan instead of reading every entry. A term without three
letters or digits in a row has no trigram to look up (``indexable``): it
compares expressions no index holds and reads the entries the other filters
leave, as before. The read path bounds the whole read by a statement timeout
(``log_views.READ_SECONDS``); #536 and #765 record the measurements.
"""

import re

from django.db.models import (
    BooleanField,
    Exists,
    F,
    Func,
    OuterRef,
    Q,
    Subquery,
    TextField,
    Value,
)
from django.db.models.lookups import IContains

from .log_descriptions import (
    DESCRIPTIONS,
    DIRECT_AUDIT_TYPES,
    OUTCOME_DESCRIPTIONS,
    PREFIXES,
    describe,
)
from .log_rows import DETAIL_FIELDS, DETAIL_LIMIT, EVENTS
from .schemas import MINISTRY_LIST_FIELDS

# Every type the page can explain by name; types outside these still match
# by their stored name.
KNOWN_TYPES = frozenset(
    {*EVENTS, *DESCRIPTIONS, *DIRECT_AUDIT_TYPES}
    | {event for event, _outcome in OUTCOME_DESCRIPTIONS}
)
# Every stored value as one JSON array's text: cheap, and never fewer matches
# than the exact condition, so it only narrows the rows that one reads.
EVERY_VALUE = "$.*"
# One shown value per row: a whole number, a Boolean or a short string as it
# is stored, or each whole number of a list of whole numbers, of a reviewed
# field. This mirrors ``log_rows._details``, which renders exactly these (a
# fraction, or a list holding anything else, is never shown, so never found).
SHOWN_VALUE_SQL = """EXISTS (
    SELECT 1 FROM jsonb_each({column}) AS field(key, value)
    CROSS JOIN LATERAL (
        SELECT field.value #>> '{{}}' AS text
        WHERE jsonb_typeof(field.value) = 'boolean'
           OR (jsonb_typeof(field.value) = 'number'
               AND field.value::numeric = trunc(field.value::numeric))
           OR (jsonb_typeof(field.value) = 'string'
               AND length(field.value #>> '{{}}') <= %s)
        UNION ALL
        SELECT item FROM jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(field.value) = 'array'
                  AND NOT jsonb_path_exists(
                      field.value,
                      'strict $[*] ? (@.type() != "number" || @ != @.floor())')
                 THEN field.value ELSE '[]'::jsonb END
        ) AS item
    ) AS shown
    WHERE field.key = ANY(%s) AND shown.text ILIKE %s
)"""


# pg_trgm takes trigrams only from runs of letters and digits, so a pattern
# without three of them in a row gives it nothing to look up: a trigram
# index then reads every one of its entries, slower than the table, and
# PostgreSQL's estimate does not know that (#765 measured 5 s against 3 s
# without the indexes at 2,000,000 entries).
TRIGRAM = re.compile(r"[^\W_]{3}")


def indexable(text):
    """Whether a trigram index can narrow a search for ``text``."""
    return TRIGRAM.search(text) is not None


def like_pattern(text):
    """An ILIKE pattern finding ``text`` literally anywhere in a value."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class ContextValues(Func):
    """The text of a JSON array of a context's values, selected by a JSON path.

    The path is a constant of this module, never reader input; the reader's
    text is compared with ``icontains``, which escapes LIKE wildcards.
    """

    function = "jsonb_path_query_array"
    template = "%(function)s(%(expressions)s)::text"
    output_field = TextField()

    def __init__(self, column, path):
        super().__init__(column, Value(path))


class Unindexed(Func):
    """``expression || ''``: the same text, as an expression no index holds.

    A search the trigram indexes cannot narrow (``indexable``) compares this
    instead, so PostgreSQL keeps the plan it used before the indexes (#765)
    while every row matches exactly as it would otherwise.
    """

    template = "(%(expressions)s || '')"
    output_field = TextField()


class ShownValueContains(Func):
    """Whether one shown value of a reviewed field contains ``text``.

    The reader's text is a bound parameter, escaped for ILIKE; the SQL and
    the field list are this module's own.
    """

    conditional = True
    output_field = BooleanField()

    def __init__(self, column, text):
        super().__init__(column)
        self.text = text

    def as_sql(self, compiler, connection, **extra_context):
        """Compile the column, then bind the limit, fields and pattern."""
        column, params = compiler.compile(self.get_source_expressions()[0])
        return SHOWN_VALUE_SQL.format(column=column), (
            *params,
            DETAIL_LIMIT,
            sorted(DETAIL_FIELDS),
            like_pattern(self.text),
        )


class AmongSubquery(Func):
    """Whether ``column`` is one of the values an uncorrelated subquery lists.

    Compiled as ``column = ANY(ARRAY(subquery))``. PostgreSQL runs the
    subquery once, as an InitPlan, and the comparison is then an ordinary
    array condition that an index on ``column`` can serve, so it can join
    the other arms of an OR in one bitmap scan (#765). ``IN (subquery)`` or
    a correlated ``EXISTS`` inside an OR cannot: each becomes a SubPlan that
    is checked against every row the rest of the query reads.
    """

    conditional = True
    output_field = BooleanField()

    def __init__(self, column, queryset):
        super().__init__(F(column), Subquery(queryset))

    def as_sql(self, compiler, connection, **extra_context):
        """Compile the column and the parenthesised subquery around ANY(ARRAY)."""
        column, subquery = self.get_source_expressions()
        column_sql, column_params = compiler.compile(column)
        subquery_sql, subquery_params = compiler.compile(subquery)
        return (
            f"{column_sql} = ANY(ARRAY{subquery_sql})",
            (*column_params, *subquery_params),
        )


def described_types(text):
    """Known types, and trigger type prefixes, whose own explanation holds
    ``text``; outcome-specific explanations are ``type_condition``'s.

    Matched in Python against the closed description tables, in the active
    language, so the database is asked only for a list of types.
    """
    needle = text.casefold()
    types = {
        event for event in KNOWN_TYPES if needle in str(describe(event)).casefold()
    }
    prefixes = [
        prefix for prefix, sentence in PREFIXES if needle in str(sentence).casefold()
    ]
    return types, prefixes


def type_condition(text, *, type_column, outcomes=False):
    """Q matching ``text`` in the stored type or the explanation shown.

    With ``outcomes`` (operational entries, whose explanation can depend on
    the recorded outcome), a type with an outcome-specific sentence matches
    by that sentence for entries with that outcome and by its own sentence
    for the others, exactly as ``describe`` chooses. Audit entries are
    always explained by their type's own sentence.
    """
    types, prefixes = described_types(text)
    special = OUTCOME_DESCRIPTIONS if outcomes else {}
    if indexable(text):
        matched = Q(**{f"{type_column}__icontains": text})
    else:
        matched = Q(IContains(Unindexed(F(type_column)), text))
    plain = types - {event for event, _outcome in special}
    if plain:
        matched |= Q(**{f"{type_column}__in": sorted(plain)})
    # Each type with outcome sentences: its entries with one of those
    # outcomes match by that outcome's sentence, and all its other entries
    # (none of those outcomes) by the type's own sentence.
    for event in sorted({event for event, _outcome in special}):
        overridden = Q()
        for (owner, outcome), sentence in special.items():
            if owner != event:
                continue
            mine = Q(context__contains={"outcome": outcome})
            overridden |= mine
            if text.casefold() in str(sentence).casefold():
                matched |= Q(**{type_column: event}) & mine
        if event in types:
            matched |= Q(**{type_column: event}) & ~overridden
    for prefix in prefixes:
        matched |= Q(**{f"{type_column}__startswith": prefix})
    return matched


def detail_condition(text):
    """Q over a ``context`` column: a shown detail value contains ``text``.

    Needs the ``every_value`` alias (``with_every_value``). The cheap
    condition comes first; PostgreSQL also costs the EXISTS higher, so the
    exact one runs only on the rows the cheap one passes. The cheap one reads
    JSON text, where a double quote or backslash in a value is escaped, so it
    could miss text holding either; such a search uses the exact one alone.
    """
    exact = ShownValueContains("context", text)
    if '"' in text or "\\" in text:
        return Q(exact)
    return Q(every_value__icontains=text) & exact


def with_every_value(rows, text):
    """``rows`` with the text of its ``context`` column's values as an alias.

    It is the indexed expression only when a trigram index can narrow a
    search for ``text`` (``indexable``).
    """
    values = ContextValues("context", EVERY_VALUE)
    return rows.alias(every_value=values if indexable(text) else Unindexed(values))


def ministry_condition(duid):
    """Q over a ``context`` column naming the Ministry ``duid``.

    A single Ministry (``ministry_duid``) or any of the sorted Ministry lists,
    which include the retained result scope of a Ministry export (reports
    spec, Ministry change summary), by JSONB containment.
    """
    matched = Q(context__contains={"ministry_duid": duid})
    for field in sorted(MINISTRY_LIST_FIELDS):
        matched |= Q(context__contains={field: [duid]})
    return matched


def narrowed(rows, query, *, type_column, contexts=None):
    """Apply the text and Ministry filters to one source's queryset.

    ``type_column`` names the source's type (``event`` or ``event_type``).
    An operational entry holds its own ``context`` (``contexts`` is None).
    An audit event's context is a separate row, which some SQL triggers never
    write, so ``contexts`` is that model's queryset: a join would drop an
    event without context even when its type matches. The subject filter is
    audit-only and applied by the caller.

    The audit text's detail arm sits in an OR with the type arms. When the
    indexes can narrow the search, it lists the matching contexts' events
    once (``AmongSubquery``), so the whole OR is one bitmap scan (#765).
    Otherwise it stays an EXISTS correlated on the context's ``event``,
    which PostgreSQL hashes: a scan with ``= ANY(array)`` as a filter would
    compare every row with every listed event (#765 measured 15 s). The
    Ministry filter stands alone, so its EXISTS becomes a semi-join.
    """
    if query.text:
        detail = detail_condition(query.text)
        if contexts is None:
            rows = with_every_value(rows, query.text)
        else:
            matched = with_every_value(contexts, query.text).filter(detail)
            if indexable(query.text):
                detail = Q(AmongSubquery("pk", matched.values("event")))
            else:
                detail = Exists(matched.filter(event=OuterRef("pk")))
        typed = type_condition(
            query.text, type_column=type_column, outcomes=contexts is None
        )
        rows = rows.filter(typed | detail)
    if query.ministry:
        condition = ministry_condition(int(query.ministry))
        if contexts is not None:
            condition = Exists(contexts.filter(condition, event=OuterRef("pk")))
        rows = rows.filter(condition)
    return rows
