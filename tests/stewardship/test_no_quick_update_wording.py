"""The Admin UI says nothing of quick updates (#1122, #1147).

ParishSoft's list of changed Families returns nothing (#465), so quick
updates (delta refreshes) brought no data. The Administrator decided
(2026-10-12) that the Admin pages stop mentioning them (#1122), and #1147
removed the capability. This guard reads every template's text and every
translatable string in the stewardship package and refuses the words,
except history: the names and descriptions of past quick updates, which the
Activity log and task pages still show.
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

# Translatable strings that may name quick updates, by file.
GETTEXT_ALLOWED = {
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
        seen.add(path)
        text = _template_wording(path.read_text())
        name = path.relative_to(PACKAGE).as_posix()
        found += [f"{name}: {match.group(0)!r}" for match in WORDS.finditer(text)]
    assert found == []
    assert len(seen) > 100


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
    """Words the Admin UI shows come from translatable strings; only history
    may name quick updates."""
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
