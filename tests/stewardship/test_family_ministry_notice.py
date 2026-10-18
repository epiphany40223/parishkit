"""The Family form's Ministry step tells Families what a request shares (#1625)."""

from pathlib import Path

import parishkit.stewardship.accounts as accounts

NOTICE = (
    "If you choose to join a ministry or stop participating in one, that "
    "ministry's leaders will see this person's email address and phone numbers "
    "so they can follow up."
)
SCRIPT = Path(accounts.__file__).parent / "static/stewardship/family-v1.js"


def test_contact_notice_is_drawn_once_before_the_ministry_choices():
    """The sentence follows the step heading and precedes every choice control."""
    script = SCRIPT.read_text()
    assert script.count(NOTICE) == 1
    editor = script[script.index("function ministryEditor(") :]
    heading = editor.index('node("h4", "Ministry participation", panel);')
    notice = editor.index(f'node("p", "{NOTICE}", panel);')
    # Always drawn (no condition), between the heading and the first choice.
    assert heading < notice < editor.index('"Continue in this ministry"')
    assert notice < editor.index("joinLabel()")
