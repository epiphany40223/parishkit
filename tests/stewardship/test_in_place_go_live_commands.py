"""The go-live command buttons act in place (#519 PR 7c-3): markup the shared
mechanism in ui-v1.js relies on.

Each command posts to its own page, whose view redirects back to it (or, for
Check readiness, re-renders it), so the forms only opt in: a key, a message,
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
            "go-live-readiness.html",
            "readiness-check",
            "{% url 'admin:go_live' %}#readiness-preview",
            "Readiness checked.",
            None,
        ),
        (
            "go-live-cleanup.html",
            "cleanup-retry",
            "{% url 'admin:go_live_cleanup' status.request_id %}#cleanup-progress",
            "Cleanup retry started.",
            "refresh",
        ),
        (
            "go-live-cleanup.html",
            "cleanup-cancel",
            "{% url 'admin:go_live_cleanup' status.request_id %}#cleanup-progress",
            "Cleanup cancel requested.",
            "refresh",
        ),
        (
            "go-live-links.html",
            "links-prepare",
            "{% url 'admin:go_live_links' transition.pk %}#{{ table.anchor }}",
            "Link preparation started.",
            None,
        ),
        (
            "go-live-links.html",
            "links-cancel-{{ record.preparation.pk }}",
            "{% url 'admin:go_live_links' transition.pk %}#{{ table.anchor }}",
            "Discarding these links.",
            None,
        ),
        (
            "go-live-links.html",
            "links-retry-{{ record.preparation.pk }}",
            "{% url 'admin:go_live_links' transition.pk %}#{{ table.anchor }}",
            "Preparation retry started.",
            None,
        ),
        (
            "go-live-links.html",
            "links-retry-cleanup-{{ record.preparation.pk }}",
            "{% url 'admin:go_live_links' transition.pk %}#{{ table.anchor }}",
            "Disposal retry started.",
            None,
        ),
        (
            "production-progress.html",
            "production-retry",
            "{% url 'admin:production_progress' %}#production-status",
            "Mail preparation retry started.",
            "refresh",
        ),
    ],
)
def test_go_live_commands_post_in_place(name, key, action, message, fallback):
    """Each command is a data-in-place POST to its own page's region, with
    the message the live region reads. Where the button goes away once its
    request is taken (cleanup and Production retries), focus falls back to
    the page's Refresh link just below instead of the region's heading."""
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


def test_every_go_live_post_form_is_in_place_except_start_cleanup():
    """No same-page command on these pages reloads any more. Start Testing
    cleanup is the one exception: it opens the cleanup page, a navigation."""
    for name in (
        "go-live-readiness.html",
        "go-live-cleanup.html",
        "go-live-links.html",
        "production-progress.html",
    ):
        text = (TEMPLATES / name).read_text()
        plain = re.findall(
            r"<form method=\"post\"(?![^>]*data-in-place)[^>]*>.*?</form>", text, re.S
        )
        assert [form for form in plain if 'value="cleanup"' not in form] == [], name


def test_check_readiness_reads_out_its_result():
    """Check readiness announces the check's own result line, whichever of
    passed or failed the page draws, both under the one id it names."""
    text = (TEMPLATES / "go-live-readiness.html").read_text()
    form = in_place_form(text, "readiness-check")
    assert 'data-in-place-message-from="origin-check"' in form
    assert text.count('id="origin-check"') == 2
