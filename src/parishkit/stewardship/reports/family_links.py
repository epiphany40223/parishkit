"""Family record ids for the shared Family-name link (#1570).

Every Admin table that names a Family draws the name with the shared
``{% family_link %}`` tag (``accounts/templatetags/stewardship.py``), which
links it to the Family's Timeline. The Timeline address carries the Family's
campaign record id (``FamilyCampaign.pk``), which most report rows do not
hold: they know the Family by its ParishSoft DUID. These helpers resolve a
whole page's DUIDs in one indexed read, never one per row, so a table's
query count does not grow with its rows.

This module stays free of view imports so pages in any app can use it.
"""

from __future__ import annotations

from django.db.models import Subquery

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign


def family_record_ids(campaign_id, duids):
    """Map each Family DUID in ``duids`` to its record id in ``campaign_id``.

    One read for all of them (the ``(campaign, family_duid)`` unique index);
    none at all when ``duids`` holds no DUID. A DUID with no campaign record
    (a Family the campaign has not loaded) is simply absent. ``campaign_id``
    None means the current campaign, the one the Timeline address serves,
    for pages outside any campaign (Refused addresses): a subquery, so it
    stays one read.
    """
    duids = {duid for duid in duids if duid is not None}
    if not duids:
        return {}
    if campaign_id is None:
        from parishkit.stewardship.accounts.runtime_models import (
            SystemConfiguration,
        )

        campaign_id = Subquery(
            SystemConfiguration.objects.values("current_campaign_id")[:1]
        )
    return dict(
        FamilyCampaign.objects.filter(
            campaign_id=campaign_id, family_duid__in=duids
        ).values_list("family_duid", "pk")
    )


def link_families(campaign_id, rows, key="family_duid"):
    """Give each row dict its Family's record id, as ``family_id``, in place.

    ``key`` names the row's DUID field. A row whose Family has no campaign
    record gets None, so ``family_link`` draws its name unlinked. Pass only
    the rows the page draws (its current page), so the one read stays small.
    Returns ``rows``.
    """
    ids = family_record_ids(campaign_id, (row.get(key) for row in rows))
    for row in rows:
        row["family_id"] = ids.get(row.get(key))
    return rows
