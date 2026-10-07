"""Parish data group URLs: settings, logos, Ministries, hosted files, refresh.

Pages end in ``/``; their form actions and images sit under them as nouns
(``uploads/``, ``deletion/``, ``name/``). Campaign Ministries moves here with
the campaign URL slice (NAV-10), because its old address names a campaign.
"""

from django.urls import path

from ..accounts import (
    branding_views,
    hosted_file_views,
    ministry_views,
    parish_views,
    refresh_views,
)

patterns = [
    path("parish/settings/", parish_views.parish_settings, name="parish_settings"),
    path("parish/logos/", branding_views.branding_settings, name="branding_settings"),
    path(
        "parish/logos/<uuid:bundle_id>/",
        branding_views.branding_preview,
        name="branding_preview",
    ),
    path(
        "parish/logos/assets/<uuid:asset_id>.png",
        branding_views.branding_asset,
        {"private": True},
        name="branding_asset",
    ),
    path("parish/ministries/", ministry_views.ministry_activity, name="ministries"),
    path("parish/files/", hosted_file_views.library, name="hosted_files"),
    path("parish/files/uploads/", hosted_file_views.upload, name="hosted_file_upload"),
    path("parish/files/deletion/", hosted_file_views.delete, name="hosted_file_delete"),
    path(
        "parish/files/<uuid:file_id>/name/",
        hosted_file_views.rename,
        name="hosted_file_rename",
    ),
    path(
        "parish/parishsoft-refresh/",
        refresh_views.source_refresh,
        name="source_refresh",
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
