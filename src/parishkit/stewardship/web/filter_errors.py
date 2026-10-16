"""Report filter refusals shown at their field (#1332).

A report's filters are checked twice: in the browser, whose complete gate
holds Apply while a rule fails, and by the server, which stays the
authority. A request that reaches the server with such a rule broken (a
hand-built one, or a browser whose gate did not run) used to get the
report's error page. A rule the reader can fix at one field now raises
``FilterError`` instead, and the report is drawn again, answered 400, with
the submitted values in its filters and the error beside that field and in
the page's error summary (UX conventions, Field errors and live checks).

A refusal no field can show (an unknown or repeated field, a choice no
form offers, a sort or page size the table does not accept) stays a plain
``ValueError`` and keeps the report's error page.
"""

from .refusals import with_status_code


class FilterError(ValueError):
    """A report filter refused at its field.

    ``field`` is the query's own field name, ``message`` reviewed static
    text (never built from the submitted value) and ``query`` the query as
    submitted, not validated, for drawing the filter form again. ``pair``
    is the other end of a range refused for its order: both ends are
    marked and share the one message, so fixing either clears it. It is a
    ``ValueError``, so every caller that refused the filters before (an
    export, a ``pk-admin`` command) still refuses them.
    """

    def __init__(self, field, message, query, *, pair=None):
        """Keep the field(s), the reviewed message and the submitted query."""
        super().__init__(str(message))
        self.field, self.message, self.query = field, message, query
        self.fields = (field,) if pair is None else (field, pair)


def error_context(error, ids, lines):
    """The report template's context for ``error``.

    ``filter_error`` names the fields to mark (the ``filter_mark`` tag) and
    the reserved message line they are described by (``filter_line``);
    ``errors`` is the base template's error summary, linking to the field.
    ``ids`` maps each query field that can be refused to its element id and
    ``lines`` to its line's name.
    """
    return {
        "filter_error": {
            "fields": error.fields,
            "line": lines[error.field],
            "message": error.message,
        },
        "errors": [{"field_id": ids[error.field], "message": error.message}],
    }


def line_sizes(messages, lines):
    """Each message line's longest message, which its reserved space fits.

    ``messages`` and ``lines`` map query fields to their message and line,
    so a line keeps the height of whichever message it may show and the
    fields below it never move when one appears (No layout shift).
    """
    sizes = {}
    for field, line in lines.items():
        text = str(messages[field])
        if len(text) > len(sizes.get(line, "")):
            sizes[line] = text
    return sizes


def refused(response):
    """Answer a report drawn again for a ``FilterError`` as its 400 refusal.

    The page is the view's own refusal, so the security boundary keeps it
    rather than replacing it with bare error text, and the refusal log
    records it with the ``invalid`` code, as the error page it replaces
    was (#1403). Returns ``response``.
    """
    response.status_code = 400
    response.stewardship_safe_error = True
    return with_status_code(response)
