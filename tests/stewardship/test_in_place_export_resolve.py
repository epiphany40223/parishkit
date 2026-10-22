"""Export Retry and Cancel, and a Mail message's resolutions, act in place
(#519 PR 7c-4): markup the shared mechanism in ui-v1.js relies on.

Each command posts to its own route, whose view redirects back to the page,
so the forms only opt in: a key, a message, a focus fallback or the regions
the answer also replaces, and an action whose fragment names a region of the
same template. The fragment rule itself is checked for every template by
test_in_place_controls.py.
"""

import re
from pathlib import Path

import pytest

from .test_in_place_controls import names_region

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)


def in_place_form(text, key):
    """The opening tag of the form whose data-in-place key is ``key``."""
    found = re.search(rf'<form [^>]*data-in-place="{re.escape(key)}"[^>]*>', text)
    assert found, key
    return found.group(0)


@pytest.mark.parametrize("name", ["report-export.html", "report-exact.html"])
@pytest.mark.parametrize(
    "key, route, message",
    [
        ("export-retry", "report_export_retry", "Export retry started."),
        ("export-cancel", "report_export_cancel", "Export cancel requested."),
    ],
)
def test_export_commands_post_in_place(name, key, route, message):
    """Retry export and Cancel export are data-in-place POSTs to their own
    routes that land on the status panel; the button is usually gone once
    the request is taken, so focus falls back to the panel (the page has no
    Refresh status link since #1586)."""
    text = (TEMPLATES / name).read_text()
    form = in_place_form(text, key)
    assert f"action=\"{{% url 'admin:{route}' job.pk %}}#export-status\"" in form
    assert names_region(text, "export-status")
    assert f"data-in-place-message=\"{{% translate '{message}' %}}\"" in form
    assert "data-in-place-fallback" not in form
    assert 'data-in-place="refresh"' not in text


def test_an_export_page_offers_no_regenerate():
    """Exports are point-in-time (#1398): the status page has no Regenerate."""
    text = (TEMPLATES / "report-export.html").read_text()
    assert "regenerat" not in text.lower()


def test_delivery_resolutions_post_in_place():
    """Each answer of the Mail message page's question is a data-in-place
    POST, keyed by its choice, that lands on the outcome panel and also
    replaces the state line, history, note form and bounce panel; each
    region it names is drawn by the page (#1563)."""
    page = (TEMPLATES / "delivery.html").read_text()
    answer = (TEMPLATES / "delivery-answer.html").read_text()
    form = in_place_form(answer, "delivery-resolve-{{ row.choice }}")
    assert (
        "action=\"{% url 'admin:delivery_resolve' delivery.id %}#delivery-resolve\""
        in form
    )
    assert names_region(page, "delivery-resolve")
    only = re.search(r'data-in-place-only="([^"]*)"', form).group(1).split()
    assert only == [
        "delivery-state",
        "delivery-history",
        "delivery-resolve",
        "delivery-note",
        "bounce-report",
        "delivery-evidence",
    ]
    for region in only:
        assert names_region(page, region), region
    # The note form lists the same regions and lands on the evidence.
    note = in_place_form(page, "delivery-note-form")
    assert (
        "#delivery-evidence" in note
        and f'data-in-place-only="{" ".join(only)}"' in (note)
    )
    # The state line and every recipient line whose outcome an answer
    # changes sit in #delivery-state, so none keeps a stale label.
    start = page.index('<div id="delivery-state" data-in-place-region>')
    state = page[start : page.index("</div>", start)]
    for line in ("delivery.state|delivery_label", "email-recipients", "email-routed"):
        assert line in state, line
    # How many addresses it reached is in the outcome panel (#1563).
    assert "email-reached" not in state
    # The outcome heading takes focus after an answer, whichever it is.
    assert (
        page.count('<h2 id="outcome-heading" tabindex="-1" data-in-place-focus>') == 6
    )
    for message in (
        "Marked as delivered.",
        "Sending it again.",
        "Recorded as not sent.",
        "Note saved.",
        "Trying again.",
        "Counted as delivered.",
        "Counted as not delivered.",
    ):
        assert f'{{% translate "{message}" as message %}}' in page, message


def test_delivery_resolutions_survive_paging():
    """The outcome panel and the note form are regions only their own
    answers replace (data-in-place-own), so paging the history keeps typed
    notes, and the note disclosure stays open across a note's answer."""
    text = (TEMPLATES / "delivery.html").read_text()
    for region in ("delivery-resolve", "delivery-note"):
        tag = re.search(rf'<div id="{region}"[^>]*>', text).group(0)
        assert "data-in-place-region" in tag and "data-in-place-own" in tag
    assert '<details id="delivery-note-disclosure" data-keep-open>' in text
    # Every resolution form posts from one of the two regions.
    assert text.count("admin:delivery_resolve") == 1
    # Seven answers of the unknown, failed and still-sending questions, and
    # the two of a stuck report email's (#1872).
    assert text.count('include "stewardship/delivery-answer.html"') == 9
