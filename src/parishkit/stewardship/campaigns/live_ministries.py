"""The one live structural exemption: a locked campaign's Ministry selections (#342).

A live campaign's structural settings are locked, in Python (admission) and in
SQL (``stewardship_campaign_pointer_v1``). Its ``ministry_duids`` alone may still
change while it is scheduled or active. Removing a Ministry is always allowed
and never touches answers already given; adding one is allowed only for a
Ministry Families could see now: present in the promoted ParishSoft catalog and
locally active. SQL repeats both rules when the change activates.
"""

from parishkit.config import ConfigError

# A campaign whose Family forms can still be opened and answered.
OPEN_STATES = frozenset({"scheduled", "active"})


def live_change_admitted(state, *, locked):
    """Whether a structurally locked campaign may change its Ministry selections."""
    return locked and state in OPEN_STATES


def addable_ministries(document):
    """DUIDs that may be added now: in the promoted catalog and locally active.

    ``document`` is the configuration whose Ministry activity applies (the
    candidate during installation, the applied one in the editor). Without a
    promoted source for the configured ParishSoft organization, nothing can be
    added.
    """
    from parishkit.stewardship.accounts.ministry_activity import active_ministries
    from parishkit.stewardship.accounts.ministry_views import current_catalog
    from parishkit.stewardship.source.snapshot_models import SourceCurrent
    from parishkit.stewardship.source.version_models import SnapshotMinistry

    current = current_catalog(document, SourceCurrent.objects.first())
    if current is None:
        return frozenset()
    present = frozenset(
        int(row.source_key)
        for row in SnapshotMinistry.objects.filter(
            snapshot_id=current.snapshot_id
        ).select_related("payload")
        # SQL's visibility rule also requires the catalog presence flag.
        if row.payload.payload.get("catalog_present") is True
    )
    return active_ministries(
        document, organization_id=current.organization_id, catalog_duids=present
    )


def check_live_selection(campaign, before, after, document):
    """Refuse a live Ministry selection change SQL would also refuse.

    ``campaign`` is the locked campaign row, ``before`` and ``after`` its
    selections, and ``document`` the candidate configuration.
    """
    if not live_change_admitted(campaign.state, locked=campaign.structural_locked):
        raise ConfigError("Campaign structural settings are locked.")
    if set(after) - set(before) - addable_ministries(document):
        raise ConfigError("Only current, active Ministries can be added.")
