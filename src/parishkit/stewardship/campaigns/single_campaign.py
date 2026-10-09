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

    The one campaign is created in the setup wizard only (decision 11).
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


def no_current_refused():
    """The refusal for a report when there is no current campaign at all.

    A report's address names no campaign, so a bookmark opened with none
    current is told so plainly, not that "this" campaign stopped being it.
    """
    return UserFacingGone(
        _("There is no current campaign."),
        fix=_("Reports show the current campaign once there is one."),
    )


def refuse_campaign_creation(patch):
    """Refuse a configuration change that adds a campaign record.

    Creating a draft and copying one both add a ``campaigns`` record; every
    other editor only updates the current campaign. The setup wizard builds
    its first campaign through its own candidate, not through this check.
    """
    if any(
        item.get("section") == "campaigns" and item.get("operation") == "add"
        for item in patch
    ):
        raise creation_refused()
