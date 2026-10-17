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
    the request is taken, so focus falls back to Refresh status below."""
    text = (TEMPLATES / name).read_text()
    form = in_place_form(text, key)
    assert f"action=\"{{% url 'admin:{route}' job.pk %}}#export-status\"" in form
    assert names_region(text, "export-status")
    assert f"data-in-place-message=\"{{% translate '{message}' %}}\"" in form
    assert 'data-in-place-fallback="refresh"' in form
    assert 'data-in-place="refresh"' in text


def test_an_export_page_offers_no_regenerate():
    """Exports are point-in-time (#1398): the status page has no Regenerate."""
    text = (TEMPLATES / "report-export.html").read_text()
    assert "regenerat" not in text.lower()


def test_delivery_resolutions_post_in_place():
    """Each resolution is a data-in-place POST, keyed by its action, that
    lands on the Admin evidence and also replaces the state line, history,
    forms and bounce panel; each region it names is drawn by the page."""
    text = (TEMPLATES / "delivery.html").read_text()
    form = in_place_form(text, "delivery-resolve-{{ command.action }}")
    assert (
        "action=\"{% url 'admin:delivery_resolve' delivery.id %}#delivery-evidence\""
        in form
    )
    assert names_region(text, "delivery-evidence")
    only = re.search(r'data-in-place-only="([^"]*)"', form).group(1).split()
    assert only == [
        "delivery-state",
        "delivery-history",
        "delivery-resolve",
        "bounce-report",
    ]
    for region in only:
        assert names_region(text, region), region
    # The state line and every recipient line whose outcome a resolution
    # changes sit in #delivery-state, so none keeps a stale label.
    start = text.index('<div id="delivery-state" data-in-place-region>')
    state = text[start : text.index("</div>", start)]
    for line in ("delivery.state|delivery_label", "email-recipients", "email-routed"):
        assert line in state, line
    assert "email-reached" in state
    for message in (
        "Evidence note saved.",
        "Delivery confirmed.",
        "Recorded as not sent.",
        "Resend authorized.",
    ):
        assert f"{{% translate '{message}' %}}" in form


def test_delivery_resolutions_survive_paging():
    """The resolution forms sit in a region only their own answers replace
    (data-in-place-own), so paging the history keeps typed evidence, and the
    note disclosure stays open across a note's answer."""
    text = (TEMPLATES / "delivery.html").read_text()
    tag = re.search(r'<div id="delivery-resolve"[^>]*>', text).group(0)
    assert "data-in-place-region" in tag and "data-in-place-own" in tag
    body = text[text.index(tag) :]
    body = body[: body.index("{% comment %}Report a bounced address")]
    assert body.count("<form ") == 1 and 'data-in-place="delivery-resolve-' in body
    assert '<details id="delivery-note-disclosure" data-keep-open>' in body
    # No resolution form is left outside the region.
    assert text.count("admin:delivery_resolve") == 1
