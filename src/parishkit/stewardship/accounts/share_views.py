"""Versioned share-option editing; live, only the short labels change (#1569).

The editor is ``option_editing``'s, reviewed and applied in the review dialog
(#750).
"""

from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from .option_editing import OptionEditor, option_settings

EDITOR = OptionEditor(
    key="share_options",
    module="financial",
    page="share_settings",
    template="stewardship/share-settings.html",
    review_template="stewardship/share-options-review.html",
    salt="stewardship-share-options-preview-v1",
    unchanged=_("No share options have changed."),
    unavailable="Financial share options are not currently editable.",
    current=lambda campaign: campaign.active_configuration.values["share_options"],
)


@require_http_methods(["GET", "HEAD", "POST"])
def share_settings(request, campaign_id):
    """Current Admins edit a draft's options, or a live campaign's short labels."""
    return option_settings(request, campaign_id, EDITOR)
