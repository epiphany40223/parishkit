"""Review work and Reports and queries URLs: every report page and its form actions.

Pages end in ``/`` and name no campaign: reports show the current campaign,
so each route hands its unchanged view the current campaign's id
(``current_campaign``). A report's form actions and downloads sit under it
as nouns: an export is posted to the report's ``exports/`` collection, a
follow-up is saved to its request's ``record/``, and the header's Find a
Family box posts to ``families/search/``. ``/admin/reports/`` keeps its own
meaning (the Response dashboard, or Ministry requests for a viewer who may
not open it) instead of the group's first entry. The Response dashboard
absorbed Participation (#1537); ``reports/participation/`` and its export
actions are gone, with no redirect (#864).

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
    census_publication_views,
    comparison_export_views,
    comparison_views,
    digest_views,
    directory_export_views,
    directory_views,
    emailed_views,
    export_pages,
    export_ui,
    financial_export_views,
    financial_views,
    information_export_views,
    information_views,
    limitation_views,
    ministry_export_views,
    ministry_followup_exports,
    ministry_followup_views,
    pledge_review_views,
    timeline_views,
    weekly_manual_views,
    weekly_views,
    workspace_views,
)
from ..web.admin_routes import current_campaign


def _page(route, view, name, *extra):
    """A report route whose view takes the current campaign's id."""
    return path(route, current_campaign(view), *extra, name=name)


patterns = [
    path("reports/", workspace_views.index, name="reports"),
    _page("reports/responses/", workspace_views.dashboard, "response_dashboard"),
    # The passive figures status the Response dashboard and Campaign
    # comparison poll while they are open (#1586): whether newer figures are
    # ready.
    _page(
        "reports/responses/status/",
        workspace_views.dashboard_status,
        "response_dashboard_status",
    ),
    _page(
        "reports/responses/exports/",
        export_ui.create,
        "report_export_create",
    ),
    # Past campaigns compared with the current one (#1018).
    _page("reports/comparison/", comparison_views.report, "campaign_comparison"),
    # Its download form (#1018, slice 6): a POST answered with a redirect to
    # the export's status address, which the page follows in place (#1778).
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
    # Pledges (#1686): the financial stream's review worklist, whose
    # decisions post to the page itself, and its CSV/XLSX/PDF download.
    _page("reports/financial/pledges/", pledge_review_views.page, "pledges_to_enter"),
    _page(
        "reports/financial/pledges/exports/",
        pledge_review_views.export,
        "pledges_to_enter_export",
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
    # Update ParishSoft (#528, ADM-09; renamed from Publish to ParishSoft by
    # #1688, whose old address is gone with no redirect), linked from Census
    # changes. Its Check ParishSoft posts to the page itself; Update's review
    # and Apply post to its confirmation, and Run again's to its retries,
    # each naming the plan and answering the review dialog.
    _page(
        "reports/census/update-parishsoft/",
        census_publication_views.page,
        "update_parishsoft",
    ),
    _page(
        "reports/census/update-parishsoft/confirmation/",
        census_publication_views.confirmation,
        "update_parishsoft_confirmation",
    ),
    _page(
        "reports/census/update-parishsoft/retries/",
        census_publication_views.retries,
        "update_parishsoft_retries",
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
    # The passive status a request page's Contact details poll when the
    # ParishSoft data was replaced while it was read (#1863).
    _page(
        "reports/information/contacts/status/",
        information_views.contacts_status,
        "information_contacts_status",
    ),
    # A request's Save (information_update) was retired, with no redirect
    # (#1685): decisions are made on the Additional information worklist,
    # which posts them to its own address.
    # The Members joining and leaving lists (joining/, leaving/) and their
    # export form (exports/) were retired, with no redirect (#1157): a
    # request's contact details are on its follow-up page, and the export is
    # the follow-up queue's Export these requests.
    # Ministry requests (#1133): the follow-up queue, with the totals, the
    # By Ministry counts and the follow-up packet, is the one page for
    # Ministry requests. The queue's own address (follow-up/) was retired
    # with no redirect; its request pages, bulk outcomes, export and Roster
    # changes to enter keep their addresses under it.
    _page("reports/ministries/", ministry_followup_views.queue, "ministry_report"),
    _page(
        "reports/ministries/packets/",
        ministry_export_views.create_packet,
        "ministry_packet",
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
    # POST to its request's roster/. Staff mark them from Ministry requests'
    # To enter by hand Stage: Roster changes to enter, the list once here,
    # was folded into it with no redirect (R39 finding 2).
    _page(
        "reports/ministries/follow-up/<uuid:request_id>/roster/",
        ministry_followup_views.roster,
        "ministry_followup_roster",
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
        timeline_views.timeline,
        "timeline",
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
        timeline_views.export,
        "timeline_export",
    ),
]
