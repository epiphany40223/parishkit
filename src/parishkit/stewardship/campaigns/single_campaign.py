"""The single-campaign interim: refuse multi-campaign actions until #145.

The system moves to a single campaign after this one (#145). Until that change
removes multi-campaign support, every control whose only purpose is working
with more than one campaign is greyed out with ``TIP`` and the server refuses
its action (admin-portal spec, navigation rule 10 and decisions 11, 18 and
19). The refusals live here, in campaign service code rather than in the
views. ``accounts.admin_editing.confirm`` and ``campaign_views._target`` call
them, so any caller that goes through those functions (or calls
``refuse_campaign_creation`` itself), as the ADM-11 host commands are
planned to, will get the same refusal as the pages. Note that
``accounts.privileged_actions.configuration_request`` records a patch without
this check; a command built on it must call ``refuse_campaign_creation``.

Each refusal is a ``UserFacingGone`` (HTTP 410): the action will not work
again, so the reader should not retry it.

The one exception is the deployment's first campaign (#142): system setup
creates no campaign, so **Create the campaign** may add one while no campaign
has ever existed (``first_campaign_admitted``). That is not a multi-campaign
action, and it is refused as soon as any campaign row exists.
"""

from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.web.refusals import UserFacingGone

# The tip on every greyed-out multi-campaign control. Templates take it from
# the ``multi_campaign_control`` template tag, so the wording lives here only.
TIP = _("Disabled; will be removed with the single-campaign change (#145)")

_FIX = _("It will be removed with the single-campaign change (#145).")


def copy_refused():
    """The refusal for Copy campaign (cloning an archived campaign)."""
    return UserFacingGone(_("Copying a campaign is disabled."), fix=_FIX)


def creation_refused():
    """The refusal for creating another campaign (New campaign, a successor).

    The one campaign is created by Create the campaign only, while the
    deployment has never had one (decision 11, #142).
    """
    return UserFacingGone(_("Creating another campaign is disabled."), fix=_FIX)


def not_current_refused():
    """The refusal for a report naming a campaign that is not the current one."""
    return UserFacingGone(
        _("This campaign is no longer the current campaign."),
        fix=_(
            "Reports show the current campaign only, until the single-campaign "
            "change (#145)."
        ),
    )


def refuse_campaign_creation(patch, *, first=False):
    """Refuse a configuration change that adds a campaign record.

    Creating a draft and copying one both add a ``campaigns`` record; every
    other editor only updates the current campaign. Create the campaign
    passes ``first=True`` and admits its one record under
    ``first_campaign_admitted`` instead.
    """
    added = sum(
        1
        for item in patch
        if item.get("section") == "campaigns" and item.get("operation") == "add"
    )
    if first:
        # Create the campaign's own preview adds exactly one campaign; its
        # admission (first_campaign_admitted) is checked under the work lock.
        if added != 1:
            raise creation_refused()
    elif added:
        raise creation_refused()


def first_campaign_admitted(configuration):
    """Whether Create the campaign may add the deployment's first campaign.

    ``configuration`` is the runtime ``SystemConfiguration`` row, read by the
    caller under its work lock. Creation needs Testing mode, no restore
    review, no current campaign, no campaign row in any state (so this never
    creates a successor), and a promoted ParishSoft snapshot to choose
    Ministries and funds from. Purge requests only exist for an earlier
    campaign, so "no campaign row" already excludes them.
    """
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.source.snapshot_models import SourceCurrent

    return (
        configuration.mode == "testing"
        and not configuration.restore_review_required
        and configuration.current_campaign_id is None
        and not Campaign.objects.exists()
        and SourceCurrent.objects.exclude(snapshot_id=None).exists()
    )


def first_campaign_refused():
    """The refusal when Create the campaign is no longer (or not yet) possible.

    It is a 409, whose error page would otherwise be headed "This information
    changed"; a plain load of the page is refused for the deployment's state,
    not for an out-of-date form, so it names the page instead.
    """
    from django.urls import reverse

    from parishkit.stewardship.web.refusals import UserFacingStale

    return UserFacingStale(
        _("The campaign can't be created right now."),
        fix=_(
            "Create the campaign is available once, while there is no campaign "
            "yet, the system is in Testing mode and ParishSoft data has been "
            "loaded. If a campaign already exists, open Campaign settings."
        ),
        link=str(reverse("admin:campaign_settings")),
        link_label=_("Open Campaign settings"),
        title=_("Create the campaign is unavailable"),
    )
