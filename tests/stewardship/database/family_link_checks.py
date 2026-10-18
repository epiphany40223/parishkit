"""Checks for the shared Family-name link to the Timeline (#1570).

Each Admin page that names a Family draws the name with ``{% family_link %}``
and reads its shown rows' Family record ids in one query
(``reports/family_links.py``). These helpers build the expected link and pick
that read out of a page's captured SQL, so a page's test can assert both the
link and that the read happens once, however many rows the page shows.
"""

from django.urls import reverse
from django.utils.html import escape

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign


def timeline_link(campaign, duid, name):
    """The link a viewer who may open the Timeline sees for Family ``duid``."""
    family = FamilyCampaign.objects.get(campaign=campaign, family_duid=duid)
    href = reverse("admin:timeline", args=[family.pk])
    return f'<a href="{href}">{escape(name)}</a>'


def link_reads(queries):
    """The statements in ``queries`` (a CaptureQueriesContext) that read
    Family record ids by DUID: the page's one link read."""
    return [
        query["sql"]
        for query in queries.captured_queries
        if 'FROM "stewardship_family_campaign"' in query["sql"]
        and '"family_duid" IN' in query["sql"]
    ]
