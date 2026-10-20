"""The go-live command buttons act in place (#519 PR 7c-3): markup the shared
mechanism in ui-v1.js relies on.

Each command posts to its own page, whose view redirects back to it, so the
forms only opt in: a key, a message,
a focus fallback where the button goes away, and an action whose fragment
names a region of the same template. The fragment rule itself is checked for
every template by test_in_place_controls.py.
"""

import re
from pathlib import Path

import pytest

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)


def in_place_form(text, key):
    """The opening tag of the form whose data-in-place key is ``key``."""
    found = re.search(rf'<form [^>]*data-in-place="{re.escape(key)}"[^>]*>', text)
    assert found, key
    return found.group(0)


@pytest.mark.parametrize(
    "name, key, action, message, fallback",
    [
        (
            "go-live-check.html",
            "go-live-verify",
            "{% url 'admin:go_live' %}#go-live-check",
            "Readiness checked.",
            None,
        ),
        (
            "go-live-check.html",
            "go-live-retry-stop",
            "{% url 'admin:go_live' %}#go-live-check",
            "Retrying stopping go-live.",
            None,
        ),
        (
            "go-live-prepare.html",
            "go-live-retry",
            "{% url 'admin:go_live_prepare' %}#go-live-prepare",
            "Retry started.",
            None,
        ),
        (
            "go-live-prepare.html",
            "go-live-again",
            "{% url 'admin:go_live_prepare' %}#go-live-prepare",
            "A new ParishSoft refresh was requested.",
            None,
        ),
        # Confirm Production's Refresh and prepare again posts Prepare's own
        # control; Prepare's answer has no region of this page, so the page
        # opens it (#1585 slice 4).
        (
            "go-live-confirm.html",
            "go-live-confirm-again",
            "{% url 'admin:go_live_prepare' %}#go-live-confirm",
            "A new ParishSoft refresh was requested.",
            None,
        ),
        # Production activation's Retry (#1585 slice 5): once taken, focus
        # goes to the step's heading, as on Prepare.
        (
            "production-progress.html",
            "production-retry",
            "{% url 'admin:production_progress' %}#production-status",
            "Mail preparation retry started.",
            None,
        ),
    ],
)
def test_go_live_commands_post_in_place(name, key, action, message, fallback):
    """Each command is a data-in-place POST to its own page's region, with
    the message the live region reads. Where a page names a fallback, focus
    falls back to it once the button goes away; the Go live steps name none,
    so focus goes to the step's heading."""
    text = (TEMPLATES / name).read_text()
    form = in_place_form(text, key)
    assert 'method="post"' in form
    assert f'action="{action}"' in form
    assert f"data-in-place-message=\"{{% translate '{message}' %}}\"" in form
    if fallback:
        assert f'data-in-place-fallback="{fallback}"' in form
        assert f'data-in-place="{fallback}"' in text
    else:
        assert "data-in-place-fallback" not in form


def test_every_go_live_post_form_is_in_place_or_opens_the_dialog():
    """No same-page command on these pages reloads any more. Start go-live,
    Stop go-live, Confirm Production and Cancel go-live open their review
    dialogs (#1585), whose Apply opens the page that follows."""
    for name in (
        "go-live-check.html",
        "go-live-prepare.html",
        "go-live-confirm.html",
        "production-progress.html",
    ):
        text = (TEMPLATES / name).read_text()
        plain = re.findall(
            r"<form method=\"post\"(?![^>]*data-in-place)[^>]*>.*?</form>", text, re.S
        )
        assert [form for form in plain if "data-review-open" not in form] == [], name


def test_check_readiness_reads_out_its_overall_result():
    """Check readiness announces the overall result, never only the public
    web address's: what still needs fixing, else that every check passed,
    from one line the answer draws beside the button (#1585)."""
    text = (TEMPLATES / "go-live-check.html").read_text()
    form = in_place_form(text, "go-live-verify")
    assert 'data-in-place-message-from="go-live-check-said"' in form
    assert text.count('id="go-live-check-said"') == 1
    assert "Readiness checked: every check passed." in text
    assert "origin-check" not in text
