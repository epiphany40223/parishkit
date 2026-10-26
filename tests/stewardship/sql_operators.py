"""Find the jsonb and containment operators SQL text writes unqualified (#2166).

An unqualified operator is resolved through ``search_path`` like a function
name, and PostgreSQL picks the closest argument-type match across every
schema on the path, not the first schema. New SQL therefore writes these
operators as ``OPERATOR(pg_catalog.?)`` and the like. The scan here is shared
by the frozen-file lint and the database test of the installed definer
functions.

Limits: SQL built in a string literal (``EXECUTE format('... ? ...')``) is
not scanned, since literals are blanked, and a stray quote inside a
non-body dollar-quoted string could hide code up to the next quote. No
current definer function does either.
"""

import re

# Code that is not SQL operators: line and block comments, string literals
# (standard, E'' with backslash escapes, and doubled quotes) and quoted
# identifiers. Dollar quotes are deliberately not skipped: a function body is
# dollar-quoted and its operators are the ones that matter.
NOT_CODE = re.compile(
    r"--[^\n]*"
    r"|/\*.*?\*/"
    r"|[Ee]'(?:[^'\\]|\\.|'')*'"
    r"|'(?:[^']|'')*'"
    r'|"(?:[^"]|"")*"',
    re.S,
)
# The qualified form, which the operator scan must not count.
QUALIFIED = re.compile(r"OPERATOR\s*\(\s*pg_catalog\s*\.\s*[^)\s]+\s*\)", re.I)
# The unqualified operators: ?, ?|, ?& (key existence), @> and <@
# (containment), longest first; jsonpath's @? is named whole so a finding
# reads as written.
BARE = re.compile(r"@\?|\?[|&]?|@>|<@")


def bare_operators(text):
    """Every unqualified ``?``, ``?|``, ``?&``, ``@>`` or ``<@`` in SQL text.

    Comments, string literals and quoted identifiers are blanked first (to
    the same length, so nothing joins across them), then the qualified
    ``OPERATOR(pg_catalog.x)`` forms; what is left is code.
    """
    code = NOT_CODE.sub(lambda match: " " * len(match[0]), text)
    code = QUALIFIED.sub(" ", code)
    return BARE.findall(code)
