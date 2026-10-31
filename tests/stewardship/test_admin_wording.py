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
# descriptions and report emails), label maps, downloaded-file headings and
# the sentences in ui-v1.js. KNOWN is the shrinking allowlist of today's
# occurrences. Counts must match exactly: a new occurrence fails, and a
# strings PR that removes one lowers or deletes its entry. The last strings
# PR of #2330 deletes KNOWN.

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
}
# Removed before matching: wording kept on purpose and placeholders. The
# sign-in and access-denied pages' reader may hold no role, so "parish staff
# member" stays lower case (Administrator, 2026-10-31); Google's roles and a
# shared drive's members are Google's words; {{ members }} is a variable.
KEPT = re.compile(
    r"parish staff members?|Google Workspace administrators?"
    r"|super administrators?|members? of that shared drive"
    r"|{{.*?}}|{%.*?%}|%\(\w+\)s|{\w+}"
)
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
    "publish": re.compile(r"\b[Pp]ublish(?:es|ing)?\b"),
    "update ParishSoft yourself": re.compile(r"\bParishSoft yourself\b"),
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
# Today's occurrences, by (term, path under PACKAGE): the shrinking
# allowlist. Never add an entry or raise a count; use the replacement.
KNOWN = {
    ("active Family", "accounts/templates/stewardship/campaign-comparison.html"): 1,
    ("active Family", "reports/comparison_documents.py"): 1,
    ("eligible Family", "accounts/campaign_family_test_views.py"): 1,
    ("eligible Family", "accounts/reminder_workgroup_views.py"): 2,
    ("eligible Family", "accounts/templates/stewardship/campaign-comparison.html"): 2,
    ("eligible Family", "accounts/templates/stewardship/family-email-sends.html"): 1,
    ("eligible Family", "accounts/templates/stewardship/family-send-progress.html"): 1,
    ("eligible Family", "accounts/templates/stewardship/mail-queue.html"): 1,
    ("eligible Family", "reports/comparison_documents.py"): 1,
    ("can use the portal", "accounts/templates/stewardship/data-to-fix-status.html"): 1,
    ("can use the portal", "accounts/templates/stewardship/source-form.html"): 3,
    (
        "can use the portal",
        "accounts/templates/stewardship/system-health-panel-source-form.html",
    ): 1,
    ("can use the portal", "accounts/templates/stewardship/system-health.html"): 1,
    ("can use the portal", "system_health.py"): 1,
    ("not part of this campaign's mailing", "reports/timelines.py"): 1,
    (
        "Not in ParishSoft data",
        "accounts/templates/stewardship/ministry-requests.html",
    ): 3,
    ("publish", "accounts/templates/stewardship/data-to-fix-status.html"): 1,
    (
        "update ParishSoft yourself",
        "accounts/templates/stewardship/ministry-followup.html",
    ): 2,
    (
        "update ParishSoft yourself",
        "accounts/templates/stewardship/ministry-requests.html",
    ): 1,
    ("Unresolved", "accounts/templates/stewardship/ministry-counts.html"): 4,
    ("Unresolved", "accounts/templates/stewardship/ministry-requests.html"): 2,
    ("Unresolved", "reports/ministries.py"): 1,
    (
        "Status: New, In progress, Resolved",
        "accounts/templates/stewardship/ministry-followup.html",
    ): 2,
    (
        "Status: New, In progress, Resolved",
        "accounts/templates/stewardship/ministry-packet.html",
    ): 1,
    ("Status: New, In progress, Resolved", "reports/ministries.py"): 2,
    ("Status: New, In progress, Resolved", "reports/ministry_followup.py"): 2,
    ("Status: New, In progress, Resolved", "reports/ministry_followup_views.py"): 1,
    ("Testing submissions", "accounts/admin_navigation.py"): 1,
    ("Testing submissions", "accounts/go_live_views.py"): 2,
    ("Testing submissions", "accounts/templates/stewardship/go-live-check.html"): 3,
    ("Testing submissions", "accounts/templates/stewardship/go-live-families.html"): 4,
    (
        "Testing submissions",
        "accounts/templates/stewardship/go-live-start-review.html",
    ): 3,
    ("Latest response", "accounts/templates/stewardship/financial-report.html"): 3,
    ("Latest response", "reports/financial_documents.py"): 1,
    ("Test recipient", "accounts/templates/stewardship/parish-settings.html"): 1,
    ("Test recipient", "accounts/templates/stewardship/setup-mail.html"): 1,
    ("Testing email address", "accounts/setup_forms.py"): 2,
    ("Testing email address", "accounts/templates/stewardship/restore-review.html"): 2,
    ("Testing email address", "accounts/templates/stewardship/setup-mail.html"): 1,
    ("Testing email address", "accounts/templates/stewardship/setup-step.html"): 1,
    ("Testing mailbox", "accounts/schedule_email_test.py"): 1,
    ("Testing mailbox", "accounts/static/stewardship/ui-v1.js"): 3,
    ("Testing mailbox", "accounts/templates/stewardship/campaign-mail.html"): 2,
    ("Testing mailbox", "accounts/templates/stewardship/setup-mail.html"): 1,
    ("Giving is turned on", "reports/participation_summary.py"): 1,
    (
        "Financial result pages",
        "accounts/templates/stewardship/financial-report.html",
    ): 1,
    ("Financial page", "accounts/artwork_views.py"): 2,
    ("Financial page", "accounts/content_forms.py"): 2,
    ("Financial page", "accounts/templates/stewardship/artwork-settings.html"): 2,
    (
        "Entered by Staff for the Family",
        "accounts/templates/stewardship/open-form-handoff.html",
    ): 1,
    (
        "Entered by Staff for the Family",
        "accounts/templates/stewardship/timeline-family-fields.html",
    ): 2,
    ("Entered by Staff for the Family", "reports/timelines.py"): 1,
    ("digest", "accounts/templates/stewardship/daily-digest.html"): 2,
    ("digest", "accounts/templates/stewardship/information.html"): 1,
    ("digest", "accounts/templates/stewardship/weekly-digest.html"): 1,
    ("digest", "audit/log_descriptions.py"): 1,
    ("timezone", "accounts/campaign_forms.py"): 1,
    ("timezone", "accounts/campaign_preview.py"): 1,
    ("timezone", "accounts/parish_views.py"): 3,
    ("timezone", "accounts/schedule_forms.py"): 2,
    ("timezone", "accounts/setup_drafts.py"): 1,
    ("timezone", "accounts/templates/stewardship/clone-settings.html"): 1,
    ("timezone", "accounts/templates/stewardship/daily-digest.html"): 1,
    ("timezone", "accounts/templates/stewardship/parish-settings.html"): 2,
    ("timezone", "accounts/templates/stewardship/report-exact.html"): 2,
    ("timezone", "accounts/templates/stewardship/report-export.html"): 1,
    ("timezone", "accounts/templates/stewardship/weekly-digest.html"): 1,
    ("timezone", "reports/comparison_documents.py"): 1,
    ("timezone", "reports/financial_documents.py"): 3,
    ("timezone", "reports/information_documents.py"): 3,
    ("timezone", "reports/ministry_documents.py"): 3,
    ("timezone", "reports/timeline_documents.py"): 1,
    ("Family login", "accounts/content_forms.py"): 1,
    ("Family login", "accounts/family_authentication.py"): 1,
    ("Family login", "jobs/operational_content.py"): 1,
    ("Family login", "responses/availability.py"): 2,
    ("Family timeline", "reports/export_ui.py"): 1,
    ("Family timeline", "reports/timeline_documents.py"): 2,
    ("the response dashboard", "audit/log_descriptions.py"): 3,
    ("member", "accounts/templates/stewardship/delivery-refusal.html"): 1,
    ("member", "accounts/templates/stewardship/directory.html"): 1,
    ("member", "accounts/templates/stewardship/find-family-results.html"): 1,
    ("member", "accounts/templates/stewardship/find-family.html"): 1,
    ("member", "accounts/templates/stewardship/user-timeline.html"): 3,
    ("member", "reports/timelines.py"): 1,
    ("ministry", "accounts/templates/stewardship/limitations-report.html"): 1,
    ("ministry", "accounts/templates/stewardship/limitations-worklist-filters.html"): 1,
    ("ministry", "accounts/templates/stewardship/ministry-followup.html"): 2,
    ("ministry", "accounts/templates/stewardship/ministry-packet.html"): 1,
    ("ministry", "accounts/templates/stewardship/ministry-requests.html"): 1,
    ("ministry", "accounts/templates/stewardship/ministry-worklist-export.html"): 1,
    ("ministry", "accounts/templates/stewardship/refresh-schedule-editor.html"): 1,
    ("ministry", "audit/log_descriptions.py"): 1,
    ("ministry", "reports/hub_streams.py"): 1,
    ("ministry", "reports/limitations.py"): 2,
    ("staff", "accounts/setup_help.py"): 3,
    ("staff", "accounts/templates/stewardship/campaign-ministries-review.html"): 1,
    ("staff", "accounts/templates/stewardship/logs.html"): 1,
    ("staff", "accounts/templates/stewardship/reminder-workgroup.html"): 2,
    ("staff", "accounts/templates/stewardship/setup-step.html"): 1,
    ("staff", "accounts/templates/stewardship/setup.html"): 1,
    ("administrator", "accounts/role_refusal.py"): 1,
    ("administrator", "accounts/setup_help.py"): 1,
    ("administrator", "accounts/static/stewardship/ui-v1.js"): 2,
    ("administrator", "accounts/templates/stewardship/delivery-control.html"): 1,
    ("administrator", "accounts/templates/stewardship/report-export.html"): 1,
    ("administrator", "accounts/templates/stewardship/setup-step.html"): 1,
    ("administrator", "web/error_pages.py"): 3,
}


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


def _python_strings(path):
    """A module's user-visible strings.

    Everywhere: the arguments of gettext calls, and the values of
    module-level upper-case dictionaries that are capitalized text: choice
    and status labels that are not translated
    (``STATES = {"resolved": "Resolved"}``). In a
    downloaded-file module (``*documents.py``) every string but docstrings
    too, because its column headings are plain constants.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if path.name.endswith("documents.py"):
        docstrings = _docstring_nodes(tree)
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]
    found = []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Dict)
            and all(
                isinstance(target, ast.Name) and target.id.isupper()
                for target in node.targets
            )
        ):
            # Labels only: identifiers, markup and translated values (which
            # the gettext walk below counts) are left out.
            found.extend(
                value.value
                for value in node.value.values
                if isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and value.value[:1].isupper()
            )
    for node in ast.walk(tree):
        function = getattr(node, "func", None)
        name = getattr(function, "id", None) or getattr(function, "attr", None)
        if isinstance(node, ast.Call) and name in GETTEXT:
            for argument in node.args:
                found.extend(_strings_in(argument))
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


def _occurrences():
    """Each retired term's match count, by (term, path under PACKAGE)."""
    counts = {}
    for path, strings in _admin_strings():
        name = path.relative_to(PACKAGE).as_posix()
        for term, rule in RETIRED.items():
            pattern, scope = rule if isinstance(rule, tuple) else (rule, None)
            if scope is not None and not scope.search(name):
                continue
            hits = sum(len(pattern.findall(KEPT.sub(" ", text))) for text in strings)
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
    assert "Testing submissions" in [
        text for path, strings in _admin_strings() for text in strings
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
