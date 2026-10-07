"""Versioned share-option editing; these structural values never change live.

The editor is ``option_editing``'s, reviewed and applied in place (#750).
"""

from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from .option_editing import OptionEditor, option_settings

EDITOR = OptionEditor(
    key="share_options",
    module="financial",
    page="share_settings",
    template="stewardship/share-settings.html",
    salt="stewardship-share-options-preview-v1",
    unchanged=_("No share options have changed."),
    unavailable="Financial share options are not currently editable.",
    current=lambda campaign: campaign.active_configuration.values["share_options"],
)


@require_http_methods(["GET", "HEAD", "POST"])
def share_settings(request, campaign_id):
    """Current Admins may review and request changes only for an unlocked draft."""
    return option_settings(request, campaign_id, EDITOR)
