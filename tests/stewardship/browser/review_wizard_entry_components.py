"""The review tables' Review these one at a time, on fixture pages (#2123).

Each of the five review tables (Census changes, Ministry requests, Pledges,
Additional information, Limitations) is its own module's fixture page,
rendered again here for a signed-in Staff reader: the button's template tag
reads the reader from the request, which the other fixture pages do not
have, and the reader's unfinished runs (``review_wizard_tables.listed_runs``) are
fixed, since the fixture server has no database. Every page but the
"no runs" and empty ones has two runs, so its button asks first.
"""

from unittest.mock import patch
from uuid import UUID

from django.template import Context
from django.template.loader import get_template
from django.test import RequestFactory

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports import review_wizard_tables
from parishkit.stewardship.reports.review_wizard_views import page_url

from . import (
    census_components,
    followup_components,
    information_components,
    limitations_components,
    pledges_components,
)

STAFF = Principal(UUID(int=2124), frozenset({"staff"}))
RUN = UUID(int=2125)
RUNS = (
    ("Census changes, Family 12 of 87", page_url(RUN, 12)),
    ("Pledges, Family 3 of 9", page_url(UUID(int=2126), 3)),
)
# Each table's page, by the wizard stream it starts.
PATHS = {stream: f"/wizard-entry/{stream}" for stream in review_wizard_tables.BY_KEY}
# Census changes with no unfinished run, and Ministry requests listing nothing.
NO_RUNS = "/wizard-entry/census-no-runs"
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


def _built(module, context, admin, runs):
    """``module``'s fixture pages, drawn for the reader with ``runs``."""
    with (
        patch.object(module, "render_to_string", _render),
        patch.object(
            review_wizard_tables, "listed_runs", lambda principal: (runs, False)
        ),
    ):
        return module.components(context, admin)


def components(context, admin):
    """Each table with runs, Census changes without, and an empty table."""
    pages = {}
    for stream, module, path in SOURCES:
        built = _built(module, context, admin, RUNS)
        pages[PATHS[stream]] = built[path]
        if module is followup_components:
            pages[EMPTY] = built[followup_components.NOTHING]
    pages[NO_RUNS] = _built(census_components, context, admin, ())[
        census_components.PATH
    ]
    return pages
