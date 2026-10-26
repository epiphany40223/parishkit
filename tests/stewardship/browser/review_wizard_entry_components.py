"""The review tables' Review these one at a time, on fixture pages (#2123).

Each of the five review tables (Census changes, Ministry requests, Pledges,
Additional information, Limitations) is its own module's fixture page,
rendered again here for a signed-in Staff reader: the button's template tag
reads the reader from the request, which the other fixture pages do not
have. The button never asks about unfinished runs (R52 Part W).
"""

from unittest.mock import patch
from uuid import UUID

from django.template import Context
from django.template.loader import get_template
from django.test import RequestFactory

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports import review_wizard_tables

from . import (
    census_components,
    followup_components,
    information_components,
    limitations_components,
    pledges_components,
)

STAFF = Principal(UUID(int=2124), frozenset({"staff"}))
# Each table's page, by the wizard stream it starts.
PATHS = {stream: f"/wizard-entry/{stream}" for stream in review_wizard_tables.BY_KEY}
# Ministry requests listing nothing.
EMPTY = "/wizard-entry/ministry-empty"
# The table page of each module, by the stream it draws.
SOURCES = (
    ("census", census_components, census_components.PATH),
    ("ministry", followup_components, followup_components.REQUESTS),
    ("pledges", pledges_components, pledges_components.PATH),
    ("information", information_components, information_components.PATH),
    ("limitations", limitations_components, limitations_components.PATH),
)


def _render(name, context=None, request=None, using=None):
    """``render_to_string`` with a signed-in Staff reader on the request."""
    reader = RequestFactory().get("/admin/")
    reader.principal = STAFF
    rendered = Context(context or {}, autoescape=True)
    rendered.request = reader
    return get_template(name).template.render(rendered)


def _built(module, context, admin):
    """``module``'s fixture pages, drawn for the signed-in reader."""
    with patch.object(module, "render_to_string", _render):
        return module.components(context, admin)


def components(context, admin):
    """Each table, and an empty table."""
    pages = {}
    for stream, module, path in SOURCES:
        built = _built(module, context, admin)
        pages[PATHS[stream]] = built[path]
        if module is followup_components:
            pages[EMPTY] = built[followup_components.NOTHING]
    return pages
