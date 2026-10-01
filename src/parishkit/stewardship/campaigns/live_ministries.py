"""The one live structural exemption: a locked campaign's Ministry selections (#342).

A live campaign's structural settings are locked, in Python (admission) and in
SQL (``stewardship_campaign_pointer_v1``). Its ``ministry_duids`` alone may still
change while it is scheduled or active. Removing a Ministry is always allowed
and never touches answers already given; adding one is allowed only for a
Ministry Families could see now: present in the promoted ParishSoft catalog and
locally active. SQL repeats both rules when the change activates.

The configuration installer holds no source grants, so catalog presence is
read through the SECURITY DEFINER ``stewardship_ministry_catalog_v1()``, which
only that login may execute. A removal reads no catalog at all.

A source promotion can land between installation preflight and activation and
drop a Ministry being added. Activation therefore repeats this check under the
work-order lock that promotion also takes (``live_selection_admitted``); a
change that no longer passes is recorded as a failed request
(``invalid_candidate``) and the previous configuration is selected again, as
for every refused activation. No operator recovery is needed: the
Administrator reviews the change again. Only if that Python check were bypassed
would the SQL guard refuse after the YAML was selected; the installer then
retries and keeps refusing that request, which its Administrator can cancel.
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
    candidate during installation). Without a promoted source for the
    configured ParishSoft organization, nothing can be added. Only logins
    granted ``stewardship_ministry_catalog_v1()`` may call this.
    """
    from django.db import connection

    from parishkit.stewardship.accounts.ministry_activity import active_ministries
    from parishkit.stewardship.accounts.ministry_views import current_catalog
    from parishkit.stewardship.source.snapshot_models import SourceCurrent

    current = current_catalog(document, SourceCurrent.objects.first())
    if current is None:
        return frozenset()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT ministry_duid FROM stewardship_ministry_catalog_v1() "
            "WHERE organization_id=%s",
            [current.organization_id],
        )
        present = frozenset(row[0] for row in cursor.fetchall())
    return active_ministries(
        document, organization_id=current.organization_id, catalog_duids=present
    )


def check_live_selection(campaign, before, after, document):
    """Refuse a live Ministry selection change SQL would also refuse.

    ``campaign`` is the locked campaign row, ``before`` and ``after`` its
    selections, and ``document`` the candidate configuration. The catalog is
    read only when something is added.
    """
    if not live_change_admitted(campaign.state, locked=campaign.structural_locked):
        raise ConfigError("Campaign structural settings are locked.")
    added = set(after) - set(before)
    if added and added - addable_ministries(document):
        raise ConfigError("Only current, active Ministries can be added.")


def live_selection_admitted(document, campaign_id):
    """Recheck a live selection change at activation, under the work-order lock.

    True unless ``document`` changes the locked current campaign's selections
    in a way ``check_live_selection`` now refuses.
    """
    from .models import Campaign

    if campaign_id is None:
        return True
    campaign = (
        Campaign.objects.select_related("active_configuration")
        .filter(pk=campaign_id)
        .first()
    )
    record = next(
        (
            row
            for row in document["sections"].get("campaigns", [])
            if row["id"] == str(campaign_id)
        ),
        None,
    )
    if campaign is None or record is None or not campaign.structural_locked:
        return True
    before = campaign.active_configuration.values.get("ministry_duids")
    after = record["values"].get("ministry_duids")
    if type(after) is not list or after == before:
        return True
    try:
        check_live_selection(campaign, before, after, document)
    except ConfigError:
        return False
    return True
