"""Responses and reports group URLs: every report page and its form actions.

Pages end in ``/`` and name no campaign: reports show the current campaign,
so each route hands its unchanged view the current campaign's id
(``current_campaign``). A report's form actions and downloads sit under it
as nouns: an export is posted to the report's ``exports/`` collection, a
follow-up is saved to its request's ``record/``, and the header's Find a
Family box posts to ``families/search/``. ``/admin/reports/`` keeps its old
meaning (Participation, or Ministry requests for a viewer who may not open
Participation) instead of the group's first entry.

Records are addressed by their own id, which already names their campaign
(NAV-12): every export, report or latest-data, shares one page at
``exports/<request>/`` with its actions under it (decision 8), and the daily
and weekly reports sent by email sit under ``emailed/``. Their old digest
addresses are gone, with no redirect (#864).
"""

from django.urls import path

from ..accounts import assisted_entry
from ..reports import (
    census_change_views,
    comparison_export_views,
    comparison_views,
    digest_views,
    directory_export_views,
    directory_views,
    emailed_views,
    exact_ui,
    export_pages,
    export_ui,
    family_timeline_views,
    financial_export_views,
    financial_views,
    information_export_views,
    information_views,
    limitation_views,
    ministry_export_views,
    ministry_followup_exports,
    ministry_followup_views,
    ministry_roster_views,
    response_dashboard,
    weekly_manual_views,
    weekly_views,
    workspace_views,
)
from ..reports import ministry_views as ministry_report_views
from ..web.admin_routes import current_campaign


def _page(route, view, name, *extra):
    """A report route whose view takes the current campaign's id."""
    return path(route, current_campaign(view), *extra, name=name)


patterns = [
    path("reports/", workspace_views.index, name="reports"),
    _page("reports/responses/", response_dashboard.dashboard, "response_dashboard"),
    _page("reports/participation/", workspace_views.participation, "participation"),
    _page(
        "reports/participation/<uuid:fact_set_id>.png",
        workspace_views.participation,
        "participation_chart",
    ),
    _page(
        "reports/participation/exports/",
        export_ui.create,
        "report_export_create",
    ),
    _page(
        "reports/participation/exact-exports/",
        exact_ui.create,
        "report_exact_create",
    ),
    # Past campaigns compared with the current one (#1018).
    _page("reports/comparison/", comparison_views.report, "campaign_comparison"),
    # Its download form (#1018, slice 6): a POST answered with the export's
    # status page.
    _page(
        "reports/comparison/exports/",
        comparison_export_views.create,
        "campaign_comparison_export",
    ),
    _page("reports/financial/", financial_views.report, "financial_report"),
    _page(
        "reports/financial/exports/",
        financial_export_views.create,
        "financial_export",
    ),
    _page("reports/limitations/", limitation_views.report, "limitations_report"),
    _page(
        "reports/limitations/exports/",
        limitation_views.export,
        "limitations_export",
    ),
    _page("reports/census/", census_change_views.report, "census_changes"),
    _page(
        "reports/census/exports/",
        census_change_views.export,
        "census_changes_export",
    ),
    _page("reports/information/", information_views.queue, "information_queue"),
    _page(
        "reports/information/exports/",
        information_export_views.create,
        "information_export",
    ),
    _page(
        "reports/information/<uuid:item_id>/",
        information_views.detail,
        "information_item",
    ),
    _page(
        "reports/information/<uuid:item_id>/record/",
        information_views.update,
        "information_update",
    ),
    # The Members joining and leaving lists (joining/, leaving/) and their
    # export form (exports/) were retired, with no redirect (#1157): a
    # request's contact details are on its follow-up page, and the export is
    # the follow-up queue's Export these requests.
    _page("reports/ministries/", ministry_report_views.report, "ministry_report"),
    _page(
        "reports/ministries/packets/",
        ministry_export_views.create_packet,
        "ministry_packet",
    ),
    _page(
        "reports/ministries/follow-up/",
        ministry_followup_views.queue,
        "ministry_followup",
    ),
    # One outcome for the requests chosen on the queue (#1131), posted by
    # its confirmation dialog.
    _page(
        "reports/ministries/follow-up/bulk/",
        ministry_followup_views.bulk,
        "ministry_followup_bulk",
    ),
    # Export these requests (#1157): the queue's filtered set as a file,
    # rendered on request. A request is a UUID, so "exports" is never one.
    _page(
        "reports/ministries/follow-up/exports/",
        ministry_followup_exports.export,
        "ministry_followup_export",
    ),
    _page(
        "reports/ministries/follow-up/<uuid:request_id>/",
        ministry_followup_views.detail,
        "ministry_followup_item",
    ),
    _page(
        "reports/ministries/follow-up/<uuid:request_id>/record/",
        ministry_followup_views.update,
        "ministry_followup_update",
    ),
    # The Entered in ParishSoft tick of a resolved join or leave (#528) is a
    # POST to its request's roster/, and Roster changes to enter, the list
    # staff work from, sits beside the queue with its downloads under it.
    _page(
        "reports/ministries/follow-up/<uuid:request_id>/roster/",
        ministry_followup_views.roster,
        "ministry_followup_roster",
    ),
    _page(
        "reports/ministries/follow-up/roster/",
        ministry_roster_views.page,
        "ministry_roster",
    ),
    _page(
        "reports/ministries/follow-up/roster/exports/",
        ministry_roster_views.export,
        "ministry_roster_export",
    ),
    # The directory's fixed actions before its Family pages; a Family is a
    # UUID, so "exports" or "search" can never be taken for one (a unit test
    # pins the resolved names).
    _page("reports/families/", directory_views.directory, "family_directory"),
    _page(
        "reports/families/exports/",
        directory_export_views.create,
        "family_directory_export",
    ),
    _page("reports/families/search/", directory_views.find_family, "find_family"),
    _page(
        "reports/families/<uuid:family_id>/",
        family_timeline_views.family_timeline,
        "family_timeline",
    ),
    # Open form's hand-off (#529): a form action, posted only, so it has no
    # slashless form.
    _page(
        "reports/families/<uuid:family_id>/open-form/",
        assisted_entry.open_form,
        "family_open_form",
    ),
    # Every export's page, report or latest-data (decision 8), and its actions.
    path(
        "reports/exports/<uuid:request_id>/", export_pages.detail, name="report_export"
    ),
    path(
        "reports/exports/<uuid:request_id>/cancellation/",
        export_pages.command,
        {"action": "cancel"},
        name="report_export_cancel",
    ),
    path(
        "reports/exports/<uuid:request_id>/retries/",
        export_pages.command,
        {"action": "retry"},
        name="report_export_retry",
    ),
    path(
        "reports/exports/<uuid:request_id>/download/",
        export_ui.command,
        {"action": "download"},
        name="report_export_download",
    ),
    path(
        "reports/exports/<uuid:request_id>/regeneration/",
        export_ui.command,
        {"action": "regenerate"},
        name="report_export_regenerate",
    ),
    # The reports sent by email: their list (ADM-12.17), then each report.
    path("reports/emailed/", emailed_views.emailed_reports, name="emailed_reports"),
    # Send a weekly report now comes before the
    # weekly reports; a report is a UUID, so "new" can never be taken for one
    # (a unit test pins the resolved names).
    _page(
        "reports/emailed/weekly/new/",
        weekly_manual_views.request_report,
        "weekly_digest_manual",
    ),
    path(
        "reports/emailed/weekly/<uuid:snapshot_id>/",
        weekly_views.snapshot,
        name="weekly_digest_snapshot",
    ),
    path(
        "reports/emailed/weekly/<uuid:snapshot_id>/items/<uuid:item_id>/",
        weekly_views.snapshot,
        name="weekly_digest_item",
    ),
    path(
        "reports/emailed/daily/<uuid:snapshot_id>/",
        digest_views.snapshot,
        name="daily_digest_snapshot",
    ),
    path(
        "reports/emailed/daily/<uuid:snapshot_id>/chart.png",
        digest_views.snapshot,
        {"representation": "png"},
        name="daily_digest_chart",
    ),
    path(
        "reports/emailed/daily/<uuid:snapshot_id>/download.png",
        digest_views.snapshot,
        {"representation": "download"},
        name="daily_digest_download",
    ),
    _page(
        "reports/families/<uuid:family_id>/exports/",
        family_timeline_views.export,
        "family_timeline_export",
    ),
]
