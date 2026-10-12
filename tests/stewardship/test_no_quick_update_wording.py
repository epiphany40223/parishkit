"""The Admin UI says nothing of quick updates (#1122).

ParishSoft's list of changed Families returns nothing (#465), so quick
updates (delta refreshes) bring no data. The Administrator decided
(2026-10-12) that the Admin pages, their help and the refresh schedule
editor stop mentioning them, while the capability stays in the code until
#1147 removes it. This guard reads every template's text and every
translatable string in the stewardship package and refuses the words,
except where they are allowed:

- the refresh schedule editor's read-only display of a stored quick rule,
  with its "Remove quick updates" action, and what that change does (the
  review's "Quick updates removed", the preview of a schedule that still
  has them);
- history: the names and descriptions of past quick updates, which the
  Activity log and task pages still show.

The ``pk-admin refresh status`` fields (``delta_succeeded_at``,
``delta_failed_at``, ``delta_refresh``) and the command line's own help are
not Admin pages; they stay until #1147.
"""

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "parishkit" / "stewardship"
WORDS = re.compile(r"quick|15-minute update|hourly update", re.IGNORECASE)
COMMENTS = re.compile(r"{% comment %}.*?{% endcomment %}|{#.*?#}", re.DOTALL)
# Translated wording, which may sit inside a tag's attribute (data-*-text,
# aria-label): a translate tag's literal and a blocktranslate's body.
TRANSLATE = re.compile(
    r"""{%\s*(?:translate|trans)\s+(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')"""
)
BLOCK = re.compile(
    r"{%\s*blocktranslate\b[^%]*%}(.*?){%\s*endblocktranslate\s*%}", re.S
)
# Markup and template code: identifiers (grid-quick, data-remove-quick,
# row.quick) live only here, never in what a reader sees.
CODE = re.compile(r"{%.*?%}|{{.*?}}|<[^>]*>", re.S)

# Template wording that may name quick updates, by file.
TEMPLATE_ALLOWED = {
    # A stored schedule that still has quick rules: the note and its action.
    "accounts/templates/stewardship/refresh-schedule-editor.html": (
        "This schedule still has quick updates, shown below without controls. "
        "ParishSoft's list of changed Families returns nothing, so they bring in "
        "no new data. Remove quick updates takes them out of the schedule; review "
        "and apply the change to stop them.",
        "Remove quick updates",
    ),
}
# Translatable strings that may name quick updates, by file.
GETTEXT_ALLOWED = {
    # A stored quick rule shown read-only, its problems, the preview of a
    # schedule that still has one, and the review of removing it.
    "accounts/refresh_schedule_forms.py": {
        "quick update",
        "Quick update",
        "%(count)d quick update",
        "%(count)d quick updates",
        "%(full)s, %(quick)s, about %(time)s of ParishSoft time",
        "About %(time)s of ParishSoft time a day (%(share)s of the day): "
        "%(full)s of about %(full_length)s%(full_typical)s and %(quick)s of "
        "about %(quick_length)s%(quick_typical)s.",
        "Quick update at %(time)s",
        "Quick update every %(step)s from %(start)s to %(end)s",
        "Quick updates added: %(times)s.",
        "Quick updates removed: %(times)s.",
    },
    # The seven-day grid's hover words for a stored quick rule's run.
    "accounts/refresh_schedule_grid.py": {"quick update"},
    # History: past quick updates in the Activity log and task pages.
    "audit/log_descriptions.py": {
        "A full ParishSoft reload was requested instead of a quick update."
    },
    "jobs/task_wording.py": {"Quick update"},
}
GETTEXT = {
    "_",
    "gettext",
    "gettext_lazy",
    "gettext_noop",
    "ngettext",
    "ngettext_lazy",
    "pgettext",
    "pgettext_lazy",
}


def _template_wording(text):
    """A template's words: its translated strings and the text outside
    markup and template code, comments dropped, one piece per line."""
    text = COMMENTS.sub(" ", text)
    pieces = [a or b for a, b in TRANSLATE.findall(text)]
    pieces += [CODE.sub(" ", body) for body in BLOCK.findall(text)]
    pieces.append(CODE.sub(" ", BLOCK.sub(" ", text)))
    return "\n".join(pieces)


def test_no_template_mentions_quick_updates():
    """Admin templates and their help say nothing of quick updates."""
    found, seen = [], set()
    for path in sorted(PACKAGE.rglob("templates/**/*.html")):
        name = path.relative_to(PACKAGE).as_posix()
        seen.add(name)
        text = _template_wording(path.read_text())
        for allowed in TEMPLATE_ALLOWED.get(name, ()):
            assert allowed in text, f"{name}: allowed wording no longer present"
            text = text.replace(allowed, " ")
        found += [f"{name}: {match.group(0)!r}" for match in WORDS.finditer(text)]
    assert found == []
    assert set(TEMPLATE_ALLOWED) <= seen and len(seen) > 100


def _translatable(path):
    """Every string literal passed to a gettext function in ``path``."""
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name in GETTEXT:
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(
                    argument.value, str
                ):
                    yield argument.value


def test_no_translatable_string_mentions_quick_updates():
    """Words the Admin UI shows come from translatable strings; only the
    stored quick rule display and history may name quick updates."""
    found, used = [], set()
    for path in sorted(PACKAGE.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        name = path.relative_to(PACKAGE).as_posix()
        for text in _translatable(path):
            if not WORDS.search(text):
                continue
            if text in GETTEXT_ALLOWED.get(name, ()):
                used.add((name, text))
            else:
                found.append(f"{name}: {text!r}")
    assert found == []
    # Every allowance is still needed, so the list shrinks with the code.
    allowed = {
        (name, text) for name, texts in GETTEXT_ALLOWED.items() for text in texts
    }
    assert allowed - used == set()


def test_the_guard_sees_what_it_refuses():
    """The guard's own reading: wording is found, identifiers are not."""
    assert WORDS.search(_template_wording("<p>Last quick update:</p>"))
    assert WORDS.search(_template_wording("<p>Last quick-update:</p>"))
    assert WORDS.search(
        _template_wording('<label>{% translate "Quick-update times" %}</label>')
    )
    assert WORDS.search(
        _template_wording(
            '<p data-x="{% blocktranslate %}It is not quick.{% endblocktranslate %}">'
        )
    )
    assert WORDS.search(_template_wording("The hourly updates are working"))
    assert not WORDS.search(
        _template_wording('<span class="grid-quick" data-remove-quick></span>')
    )
    assert not WORDS.search(_template_wording("{# quick #}{{ row.quick.words }}"))
