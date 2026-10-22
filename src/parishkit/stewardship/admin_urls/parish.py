"""Parish data group URLs: settings, logos, Ministries, hosted files, past
campaigns, refresh and Data to fix in ParishSoft.

Pages end in ``/``; their form actions and images sit under them as nouns
(``uploads/``, ``deletion/``, ``name/``, ``imports/``, ``removal/``); Past
campaigns' ``review/`` is the name its specification and the ingress rule
give the upload route. A live campaign's Ministries change on Campaign
settings (#1119), so Ministries has no campaign editor beside it.
"""

from django.urls import path

from ..accounts import (
    branding_views,
    data_to_fix_views,
    hosted_file_views,
    ministry_views,
    parish_views,
    refresh_views,
)
from ..accounts.group_root_views import group_root
from ..reports import past_campaign_views

patterns = [
    # The group root opens the first entry the viewer may open now.
    path("parish/", group_root("parish"), name="parish_root"),
    path("parish/settings/", parish_views.parish_settings, name="parish_settings"),
    path("parish/logos/", branding_views.branding_settings, name="branding_settings"),
    path(
        "parish/logos/assets/<uuid:asset_id>.png",
        branding_views.branding_asset,
        {"private": True},
        name="branding_asset",
    ),
    path("parish/ministries/", ministry_views.ministries, name="ministries"),
    path("parish/files/", hosted_file_views.library, name="hosted_files"),
    path("parish/files/uploads/", hosted_file_views.upload, name="hosted_file_upload"),
    path("parish/files/deletion/", hosted_file_views.delete, name="hosted_file_delete"),
    path(
        "parish/files/<uuid:file_id>/name/",
        hosted_file_views.rename,
        name="hosted_file_rename",
    ),
    # Past campaigns (#1018): archive workbooks of earlier campaigns.
    path("parish/past-campaigns/", past_campaign_views.page, name="past_campaigns"),
    path(
        "parish/past-campaigns/review/",
        past_campaign_views.review,
        name="past_campaign_review",
    ),
    path(
        "parish/past-campaigns/imports/",
        past_campaign_views.apply,
        name="past_campaign_import",
    ),
    path(
        "parish/past-campaigns/removal/",
        past_campaign_views.remove,
        name="past_campaign_removal",
    ),
    # Download campaign archive (#1018 part (a)): a POST answered with the
    # export's status page.
    path(
        "parish/past-campaigns/archive/",
        past_campaign_views.archive,
        name="past_campaign_archive",
    ),
    path(
        "parish/parishsoft-refresh/",
        refresh_views.source_refresh,
        name="source_refresh",
    ),
    # Load ParishSoft data's passive status while a new campaign awaits the
    # load the system starts for it (#1951): never audited, never extends
    # the sign-in.
    path(
        "parish/parishsoft-refresh/status/",
        refresh_views.campaign_load_status,
        name="campaign_load_status",
    ),
    path(
        "parish/data-to-fix/",
        data_to_fix_views.data_to_fix,
        name="parishsoft_fixes",
    ),
    # The passive status fragment the open page polls for its counts
    # (#1859): never audited, never extends the sign-in.
    path(
        "parish/data-to-fix/status/",
        data_to_fix_views.data_to_fix_status,
        name="parishsoft_fixes_status",
    ),
]

# A configuration change's status page, outside the menu groups: it stands
# under the settings page the change came from (admin-portal spec).
change_patterns = [
    path(
        "changes/<uuid:request_id>/",
        ministry_views.configuration_request,
        name="configuration_request",
    ),
]
