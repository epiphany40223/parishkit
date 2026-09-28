"""Admin pages describe outcomes, not the machinery that produces them (#200).

Configuration files, installers, key fingerprints and preview lifetimes are
implementation details. Their enforcement stays in code; the pages must not
explain them. Expired previews get a plain refusal instead (see
``web.refusals.expired_preview``).
"""

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
# The setup-finishing page still carries instructions for whoever runs the
# server itself, where these words name real things they operate on.
OPERATOR_PAGES = {"setup-cancel.html"}
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
    offending = [text for text in _messages(path) if FORBIDDEN.search(text)]
    assert offending == []
