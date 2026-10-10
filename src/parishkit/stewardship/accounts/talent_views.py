"""Versioned Member talent editing, reusing the share-option editor's machinery.

A campaign that never edited its talents shows the built-in defaults; saving
stores an explicit ``talent_options`` list (same shape as share options).
Like share options these are structural: they never change while live. The
editor is ``option_editing``'s, reviewed and applied in place (#750).
"""

from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.responses.service import default_talent_options

from .option_editing import OptionEditor, option_settings


def current_talents(campaign):
    """The campaign's talent list, or the defaults it resolves to today."""
    values = campaign.active_configuration.values
    # An explicitly emptied list stays empty; only a never-edited one defaults.
    if "talent_options" in values:
        return values["talent_options"]
    return default_talent_options()


EDITOR = OptionEditor(
    key="talent_options",
    module="ministry",
    page="talent_settings",
    template="stewardship/talent-settings.html",
    salt="stewardship-talent-options-preview-v1",
    unchanged=_("No talents have changed."),
    unavailable="Member talents are not currently editable.",
    current=current_talents,
)


@require_http_methods(["GET", "HEAD", "POST"])
def talent_settings(request, campaign_id):
    """Current Admins may review and request changes only for an unlocked draft."""
    return option_settings(request, campaign_id, EDITOR)
