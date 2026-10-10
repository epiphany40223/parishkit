"""Refresh schedule editor posts (#632), as the settings page sends them."""

from html.parser import HTMLParser

from parishkit.stewardship.accounts.refresh_schedule_forms import (
    rule_initial,
    skip_initial,
)


def rows(rules, skips=(), *, switch=False):
    """The editor's fields for ``rules`` and ``skips`` in stored form.

    Each row sends only the fields its shape uses, as the page script does
    (it disables the others); the switch is sent only when on, as a checkbox.
    """
    data = {}
    for prefix, items, initial in (
        ("rules", rules, rule_initial),
        ("skips", skips, skip_initial),
    ):
        data[f"{prefix}-TOTAL_FORMS"] = str(len(items))
        data[f"{prefix}-INITIAL_FORMS"] = "0"
        for index, item in enumerate(items):
            for name, value in initial(item).items():
                data[f"{prefix}-{index}-{name}"] = str(value)
    if switch:
        data["skip_around_family_emails"] = "on"
    return data


class _Fields(HTMLParser):
    """Collect the editor's fields from a rendered settings page.

    Text and hidden inputs by value, checked checkboxes, and each select's
    selected option (or its first); rows rendered from the ``<template>``
    are left out, as a browser never sends them.
    """

    def __init__(self):
        super().__init__()
        self.values = {}
        self.select = None
        self.first = None
        self.template = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "template":
            self.template += 1
        if self.template:
            return
        name = attrs.get("name", "")
        if not (
            name.startswith(("rules-", "skips-")) or name == "skip_around_family_emails"
        ):
            if tag == "option" and self.select is not None:
                self._option(attrs)
            return
        if tag == "input" and (attrs.get("type") != "checkbox" or "checked" in attrs):
            self.values[name] = attrs.get("value", "on")
        elif tag == "select":
            self.select, self.first = name, None

    def _option(self, attrs):
        """Remember a select's first option and take a selected one."""
        if self.first is None:
            self.first = attrs.get("value", "")
            self.values.setdefault(self.select, self.first)
        if "selected" in attrs:
            self.values[self.select] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "template":
            self.template -= 1
        elif tag == "select":
            self.select = None


def shown(html):
    """The editor's fields exactly as the page drew them (no edit)."""
    parser = _Fields()
    parser.feed(html)
    return parser.values
