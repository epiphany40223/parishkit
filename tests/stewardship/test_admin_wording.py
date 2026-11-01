"""Admin pages describe outcomes, not the machinery that produces them (#200).

Configuration files, installers, key fingerprints and preview lifetimes are
implementation details. Their enforcement stays in code; the pages must not
explain them. Expired previews get a plain refusal instead (see
``web.refusals.expired_preview``).

The second half of this file guards the words the Administrator retired from
Admin text (#2330; ui-conventions spec, "Retired words").
"""

import ast
import re
from pathlib import Path

import pytest

TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
# Words that describe internals rather than what the Admin sees happen.
FORBIDDEN = re.compile(
    r"\bYAML\b|\bversioned\b|\binstallers?\b|\bfingerprints?\b"
    r"|expires in (?:fifteen|15) minutes",
    re.IGNORECASE,
)
# The setup-finishing page (and its live status region, which draws the
# operator's step) still carries instructions for whoever runs the server
# itself, where these words name real things they operate on.
OPERATOR_PAGES = {"setup-cancel.html", "setup-finishing-status.html"}
# The backup encryption key page (#198) shows the key's fingerprint because it
# is the one thing the Administrator compares with the fingerprint recorded
# beside the private key off the server; there it is the outcome, not
# machinery. Every other forbidden word is still checked there.
FINGERPRINT_PAGES = {"backup-key.html"}
TRANSLATED = re.compile(
    r"{%\s*translate\s+\"([^\"]*)\"|{%\s*blocktranslate[^%]*%}(.*?){%\s*endblocktranslate",
    re.DOTALL,
)


def _messages(path):
    """Every user-visible translated string in one template."""
    for match in TRANSLATED.finditer(path.read_text()):
        yield match.group(1) or match.group(2)


@pytest.mark.parametrize(
    "path", sorted(TEMPLATES.glob("*.html")), ids=lambda path: path.name
)
def test_admin_templates_avoid_implementation_wording(path):
    """No template tells an Admin about files, installers or preview lifetimes."""
    if path.name in OPERATOR_PAGES:
        pytest.skip("operator instructions for the server itself")
    offending = [
        text
        for text in _messages(path)
        if FORBIDDEN.search(
            re.sub(r"(?i)\bfingerprint\b", "", text)
            if path.name in FINGERPRINT_PAGES
            else text
        )
    ]
    assert offending == []


def test_admin_templates_carry_no_sensitive_data_banner():
    """No Admin page shows a "Sensitive parish ..." download banner (#1539).

    The Administrator removed the on-page banners from export forms and the
    export status page (ui-conventions, Downloaded files). The privacy line
    inside each downloaded file is separate and stays.
    """
    offending = [
        path.name
        for path in sorted(TEMPLATES.rglob("*.html"))
        if re.search(r"trusted device|Sensitive parish", path.read_text())
    ]
    assert offending == []


# --- Retired words (#2330) -------------------------------------------------
#
# The ui-conventions spec's "Retired words" table lists every word retired
# from Admin text, with its replacement. RETIRED holds one pattern per table
# row, keyed by the row's first cell, and the scan below counts each
# pattern's matches in the user-visible Admin strings: translated template
# text, gettext strings in Python (pages, forms, messages, errors, audit
# descriptions and report emails), the capitalized text of upper-case
# constants (label maps, heading tuples, captions), downloaded-file
# headings, CLI help and descriptions, and the sentences in ui-v1.js. Other
# untranslated Python text (a plain string built inside a function, a CLI
# command's printed output) is not scanned. KNOWN, now empty, listed the
# occurrences the #2330 string changes could not remove at first, with exact
# counts: a new occurrence fails.

ROOT = TEMPLATES.parents[5]
PACKAGE = ROOT / "src/parishkit/stewardship"
SPEC = ROOT / "docs/specs/stewardship/ui-conventions/spec.md"
UI_SCRIPT = PACKAGE / "accounts/static/stewardship/ui-v1.js"
# Family-portal pages keep the Family portal's own wording (ui-conventions,
# Family portal). Every other template, including the pages both portals
# render, is Admin text.
FAMILY_TEMPLATES = {
    "family.html",
    "family-assist-refused.html",
    "family-login.html",
    "family-maintenance.html",
    "family-unavailable.html",
    "hosted-file-unavailable.html",
    # The public About, Contact, Privacy and Terms pages (#2360) are read by
    # Families and visitors, not by Administrators, so they keep plain
    # parish wording ("ministries", "staff") rather than the Admin terms.
    "public-about.html",
    "public-contact.html",
    "public-privacy.html",
    "public-terms.html",
}
# The Family sign-in page's title, which Python builds; the Family portal
# keeps its own wording there too.
FAMILY_FUNCTIONS = {"family_login_title", "_login_title"}
# Removed before matching: wording kept on purpose, CLI option names
# (``--ministry``) and placeholders. Google's roles and a shared drive's
# members are Google's words; {{ members }} is a variable.
KEPT = re.compile(
    r"Google Workspace administrators?"
    r"|super administrators?|members? of that shared drive"
    r"|--[\w-]+|{{.*?}}|{%.*?%}|%\(\w+\)s|{\w+}"
)
# The sign-in help and access-denied pages' reader may hold no role, so
# "parish staff member" stays lower case there, and only there
# (Administrator, 2026-10-31).
STAFF_MEMBER = re.compile(r"parish staff members?")
STAFF_MEMBER_PAGES = {
    "accounts/google_account_help.py",
    "accounts/templates/stewardship/denied.html",
}
# Code, not text: a lower-case identifier such as a data key
# (``source["timezone"]``) or a field name.
IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")
# Keyword arguments whose strings are CLI text: argparse help and
# descriptions.
CLI_KEYWORDS = {"help", "description", "epilog"}
# A word retired only in one area, where it has other, correct senses
# elsewhere ("unresolved refused addresses", a background task "In
# progress"), carries a path pattern too.
MINISTRY_PATHS = re.compile("ministr")
RETIRED = {
    "active Family": re.compile(r"\b[Aa]ctive[- ]Famil(?:y|ies)\b(?! group| head)"),
    "eligible Family": re.compile(
        r"\b(?:[Nn]o longer|[Nn]ot(?: currently)?) eligible\b"
        r"|(?<![-\w])eligible (?:Famil|for the (?:Family )?portal|for the campaign)"
    ),
    "can use the portal": re.compile(r"\bcan use the (?:Family )?portal\b"),
    "not part of this campaign's mailing": re.compile(r"\bcampaign's mailing\b"),
    "Not in ParishSoft data": re.compile(r"\bNot in ParishSoft data\b"),
    "No longer in this campaign (a Ministry)": (
        re.compile(r"\bNo longer in this campaign\b"),
        MINISTRY_PATHS,
    ),
    "publish": re.compile(r"\b[Pp]ublish(?:es|ing)?\b"),
    "update ParishSoft yourself": re.compile(r"\bParishSoft yourself\b"),
    # The whole string is a count of waiting work: "to do" or "N to do".
    "to do": re.compile(r"^\s*\d*\s*to do\s*$"),
    "Unresolved": (re.compile(r"\b[Uu]nresolved\b"), MINISTRY_PATHS),
    "Status: New, In progress, Resolved": (
        re.compile(r"\b(?:In progress|Resolved)\b|\bresolved and withdrawn\b"),
        MINISTRY_PATHS,
    ),
    "Testing submissions": re.compile(r"\bTesting submissions\b"),
    "Latest response": re.compile(r"\bLatest response\b"),
    "Test recipient": re.compile(r"\bTest recipient\b"),
    "Testing email address": re.compile(r"\b[Tt]esting (?:email )?address\b"),
    "Testing mailbox": re.compile(r"\b[Tt]esting mailbox\b"),
    "Giving is turned on": re.compile(r"\bGiving is turned on\b"),
    "Financial result pages": re.compile(r"\bFinancial result pages\b"),
    "Financial page": re.compile(
        r"\bFinancial (?:introduction|page)\b|\bafter Financial\b"
    ),
    "Entered by Staff for the Family": re.compile(r"\bby Staff for the Family\b"),
    "digest": re.compile(r"\b[Dd]igests?\b"),
    "timezone": re.compile(r"\b[Tt]imezones?\b"),
    "Family login": re.compile(r"\bFamily login\b"),
    "Family timeline": re.compile(r"\bFamily timeline\b"),
    "the response dashboard": re.compile(
        r"\bthe (?:response dashboard|system logs|campaign comparison)\b"
    ),
    "member": re.compile(r"\bFamily members?\b|\bmember's\b|\bmember of the Family\b"),
    "ministry": re.compile(r"\bministr(?:y|ies)\b"),
    "staff": re.compile(r"\bstaff\b"),
    "administrator": re.compile(r"\badministrators?\b"),
}
GETTEXT = {"_", "gettext", "gettext_lazy", "gettext_noop", "ngettext", "ngettext_lazy"}
TEMPLATE_STRINGS = re.compile(
    r"""{%\s*translate\s+(?:"([^"]*)"|'([^']*)')"""
    r"|{%\s*blocktranslate[^%]*%}(.*?){%\s*endblocktranslate"
    r"""|\b_\((?:"([^"]*)"|'([^']*)')\)""",
    re.DOTALL,
)
# A "/" after one of these (or after "return") starts a regular expression
# literal in JavaScript, not a division.
REGEX_BEFORE = tuple("(,=:[!&|?{};") + ("return",)
# Today's occurrences, by (term, path under PACKAGE): the allowlist, empty
# since frozen migration 0202 renamed the last one (the Family sign-in abuse
# alert's title). Never add an entry; use the replacement.
KNOWN = {}


def _template_strings():
    """Each Admin template's translated strings, by path."""
    for path in sorted(TEMPLATES.rglob("*.html")):
        if path.name not in FAMILY_TEMPLATES:
            text = path.read_text(encoding="utf-8")
            yield (
                path,
                [
                    next(group for group in match.groups() if group is not None)
                    for match in TEMPLATE_STRINGS.finditer(text)
                ],
            )


def _docstring_nodes(tree):
    """The ids of a module's docstring constants, which no reader sees."""
    return {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }


def _strings_in(node):
    """Every string constant inside one expression."""
    return [
        part.value
        for part in ast.walk(node)
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
    ]


def _skipped_nodes(tree):
    """The ids of every node inside a Family-portal function (FAMILY_FUNCTIONS)."""
    return {
        id(part)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in FAMILY_FUNCTIONS
        for part in ast.walk(node)
    }


def _constant_labels(tree):
    """The capitalized text in upper-case constants at module or class level.

    Choice and status labels that are not translated
    (``STATES = {"resolved": "Resolved"}``), and heading tuples and caption
    constants (``FAMILY_HEADINGS = ("Family", "Latest response")``). Dict
    keys, identifiers and markup are left out; translated values are counted
    by the gettext walk.
    """
    found = []
    for scope in ast.walk(tree):
        if not isinstance(scope, ast.Module | ast.ClassDef):
            continue
        for node in scope.body:
            if not isinstance(node, ast.Assign | ast.AnnAssign) or node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not all(
                isinstance(target, ast.Name) and target.id.isupper()
                for target in targets
            ):
                continue
            value = node.value
            parts = (
                value.values
                if isinstance(value, ast.Dict)
                else value.elts
                if isinstance(value, ast.Tuple | ast.List | ast.Set)
                else [value]
            )
            translated = {
                id(inner)
                for part in parts
                for call in ast.walk(part)
                if isinstance(call, ast.Call)
                for inner in ast.walk(call)
            }
            found.extend(
                text.value
                for part in parts
                for text in ast.walk(part)
                if isinstance(text, ast.Constant)
                and isinstance(text.value, str)
                and id(text) not in translated
                and text.value[:1].isupper()
            )
    return found


def _python_strings(path):
    """A module's user-visible strings.

    Everywhere: the arguments of gettext calls, CLI help and description
    keywords (CLI_KEYWORDS), and the capitalized text of upper-case constants
    (``_constant_labels``). In a downloaded-file module (``*documents.py``)
    every string too, because its column headings are plain constants, except
    docstrings and identifiers such as data keys (``source["timezone"]``).
    Family-portal functions (FAMILY_FUNCTIONS) are skipped.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skipped = _skipped_nodes(tree)
    if path.name.endswith("documents.py"):
        skipped |= _docstring_nodes(tree)
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skipped
            and not IDENTIFIER.fullmatch(node.value)
        ]
    found = _constant_labels(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in skipped:
            continue
        function = node.func
        name = getattr(function, "id", None) or getattr(function, "attr", None)
        if name in GETTEXT:
            for argument in node.args:
                found.extend(_strings_in(argument))
        for keyword in node.keywords:
            if keyword.arg in CLI_KEYWORDS:
                found.extend(_strings_in(keyword.value))
    return found


def _script_strings(text):
    """The sentence-like string literals of a script: quoted text with a space.

    A small scanner skips comments and regular expression literals, whose
    apostrophes and quotes would otherwise open false strings. A template
    literal's ``${...}`` stays inside its text, which KEPT does not strip
    but no retired pattern matches.
    """
    found, index, size, previous = [], 0, len(text), ";"
    while index < size:
        char = text[index]
        if text.startswith("//", index):
            index = text.find("\n", index) % (size + 1) or size
            continue
        if text.startswith("/*", index):
            index = text.find("*/", index + 2) + 2 or size
            continue
        if char in "'\"`" or (char == "/" and previous.endswith(REGEX_BEFORE)):
            end, in_class = index + 1, False
            while end < size and (text[end] != char or in_class):
                if char == "/" and text[end] in "[]":
                    in_class = text[end] == "["
                end += 2 if text[end] == "\\" else 1
            literal = text[index + 1 : end]
            if char != "/" and " " in literal:
                found.append(literal)
            index, previous = end + 1, "x"
            continue
        if not char.isspace():
            # The last few code characters decide whether "/" is division.
            previous = (previous + char)[-6:]
        index += 1
    return found


def _admin_strings():
    """Every scanned source's user-visible strings, by path."""
    yield from _template_strings()
    for path in sorted(PACKAGE.rglob("*.py")):
        yield path, _python_strings(path)
    yield UI_SCRIPT, _script_strings(UI_SCRIPT.read_text(encoding="utf-8"))


def _kept_removed(text, name):
    """One string with its kept wording (KEPT, STAFF_MEMBER) blanked out."""
    if name in STAFF_MEMBER_PAGES:
        text = STAFF_MEMBER.sub(" ", text)
    return KEPT.sub(" ", text)


def _occurrences():
    """Each retired term's match count, by (term, path under PACKAGE)."""
    counts = {}
    for path, strings in _admin_strings():
        name = path.relative_to(PACKAGE).as_posix()
        for term, rule in RETIRED.items():
            pattern, scope = rule if isinstance(rule, tuple) else (rule, None)
            if scope is not None and not scope.search(name):
                continue
            hits = sum(
                len(pattern.findall(_kept_removed(text, name))) for text in strings
            )
            if hits:
                counts[term, name] = hits
    return counts


def _spec_terms():
    """The first cell of every row of the spec's "Retired words" table."""
    section = SPEC.read_text(encoding="utf-8").split("\n### Retired words\n", 1)[1]
    section = section.split("\n### ", 1)[0]
    rows = re.findall(r"^\| (.+?) \|", section, re.MULTILINE)
    return {row.replace('"', "") for row in rows[2:]}


def test_retired_words_follow_the_spec_table():
    """Each row of the spec's table has exactly one guard pattern."""
    assert _spec_terms() == RETIRED.keys()
    assert {term for term, _ in KNOWN} <= RETIRED.keys()


def test_the_scan_reads_every_kind_of_admin_text():
    """The scanner finds strings in templates, Python and ui-v1.js.

    A scanner that silently found nothing would pass the guard below.
    """
    counts = {"html": 0, "py": 0, "js": 0}
    for path, strings in _admin_strings():
        counts[path.suffix[1:]] += len(strings)
    assert counts["html"] > 4000 and counts["py"] > 3000 and counts["js"] > 250
    assert "Testing responses" in [
        text for path, strings in _admin_strings() for text in strings
    ]


def test_python_scanner_reads_text_not_code(tmp_path):
    """Constants, CLI help and documents are read; keys and Family titles not.

    Heading tuples and captions are text wherever they live, a data key such
    as ``source["timezone"]`` is code, and the Family sign-in title keeps the
    Family portal's wording.
    """
    module = tmp_path / "report_documents.py"
    module.write_text(
        '"""Module docstring with a digest."""\n'
        'HEADINGS = ("Family", "Latest response", _("Kept once"))\n'
        "def write(source):\n"
        '    return source["timezone"], "Display timezone"\n'
    )
    assert _python_strings(module) == [
        "Family",
        "Latest response",
        "Kept once",
        "Display timezone",
    ]
    module = tmp_path / "views.py"
    module.write_text(
        "class Form:\n"
        '    CAPTION = "Members who cannot participate in ministries"\n'
        'parser.add_argument("--x", help="count ministries")\n'
        "def family_login_title(service):\n"
        '    return _("Family login")\n'
        'LABELS = {"key": _("Translated"), "other": "Plain label"}\n'
    )
    assert sorted(_python_strings(module)) == [
        "Members who cannot participate in ministries",
        "Plain label",
        "Translated",
        "count ministries",
    ]


def test_script_scanner_skips_comments_and_regular_expressions():
    """Quotes inside comments or regex literals never open a false string."""
    script = (
        "// a region's digest\n"
        'const name = /filename="([^"]+)"/i.exec(value);\n'
        "/* it's fine */ show('Check the Testing mailbox.');\n"
        "const half = total / 2; label(`Every ${count} Families`);\n"
    )
    assert _script_strings(script) == [
        "Check the Testing mailbox.",
        "Every ${count} Families",
    ]


def test_no_new_retired_words_in_admin_text():
    """Retired words appear only where KNOWN lists them, exactly as often.

    A new occurrence fails here: use the spec table's replacement. A strings
    PR that removes occurrences lowers or deletes their KNOWN entries, so the
    allowlist only shrinks.
    """
    found = _occurrences()
    added = {key: count for key, count in found.items() if count > KNOWN.get(key, 0)}
    removed = {key: count for key, count in KNOWN.items() if found.get(key, 0) < count}
    assert added == {}, "retired words (see ui-conventions, Retired words)"
    assert removed == {}, "fewer occurrences now: lower or delete these KNOWN entries"
