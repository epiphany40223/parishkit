"""Mail and Family portal group URLs: mail controls, progress, history, mail.

Pages end in ``/``; their form actions and status fragment sit under them as
nouns (``resolution/``, ``bounce-report/``, ``clearance/``, ``status/``).
Pause and resume mail acts on the current campaign, so its campaign-free
route hands the view the current campaign's id. The header's presence count
is a JSON read that a script polls, so it keeps the ``/admin/presence``
address (decision 8); only the page moved, and any other read there is 404
(#864).
"""

from django.http import Http404
from django.urls import path

from ..accounts import (
    delivery_control_views,
    family_maintenance_views,
    presence,
    restore_review_views,
)
from ..accounts.group_root_views import group_root
from ..jobs import delivery_views, mail_queue_views, send_history_views
from ..reports import (
    report_email_copy_views,
    report_email_editor_views,
    report_email_views,
)
from ..web.admin_routes import current_campaign

patterns = [
    # The group root opens the first entry the viewer may open now.
    path("mail/", group_root("mail"), name="mail_root"),
    path(
        "mail/controls/",
        current_campaign(delivery_control_views.control),
        name="delivery_control",
    ),
    # Every email waiting to go out, with the Family send panel (#1562); it
    # replaced Family email progress (mail/family-progress/), which is gone
    # with no redirect (#864).
    path("mail/queue/", mail_queue_views.mail_queue, name="mail_queue"),
    path(
        "mail/queue/status/",
        mail_queue_views.mail_queue_status,
        name="mail_queue_status",
    ),
    path(
        "mail/family-history/",
        send_history_views.family_email_sends,
        name="family_email_sends",
    ),
    path("mail/sent/", delivery_views.delivery_list, name="deliveries"),
    # Report emails (#1786): the list, its row actions' form actions (Pause,
    # Resume and Delete as changes; Send now as a manual send), and each
    # send's saved copy, the address every report email links, shown as the
    # email was sent until #1946 draws it as web components.
    path(
        "mail/report-emails/",
        report_email_views.report_emails,
        name="report_emails",
    ),
    path(
        "mail/report-emails/changes/",
        current_campaign(report_email_views.report_email_changes),
        name="report_email_changes",
    ),
    path(
        "mail/report-emails/manual-sends/",
        current_campaign(report_email_views.report_email_send_now),
        name="report_email_send_now",
    ),
    # New and Edit report email (#1786, delivery plan item 6), and the
    # editor's live schedule preview, a POST its script reads. "new/" and
    # "schedule-preview/" come before the report's own address, a UUID.
    path(
        "mail/report-emails/new/",
        current_campaign(report_email_editor_views.report_email_new),
        name="report_email_new",
    ),
    path(
        "mail/report-emails/schedule-preview/",
        current_campaign(report_email_editor_views.schedule_preview),
        name="report_email_schedule_preview",
    ),
    path(
        "mail/report-emails/<uuid:report_id>/",
        current_campaign(report_email_editor_views.report_email_edit),
        name="report_email_edit",
    ),
    path(
        "mail/report-emails/sends/<uuid:send_id>/",
        report_email_copy_views.saved_copy,
        name="report_email_send",
    ),
    path(
        "mail/report-emails/sends/<uuid:send_id>/images/<str:name>.png",
        report_email_copy_views.saved_image,
        name="report_email_send_image",
    ),
    path(
        "mail/report-emails/sends/<uuid:send_id>/items/<uuid:item_id>/",
        report_email_copy_views.saved_item,
        name="report_email_send_item",
    ),
    path(
        "mail/sent/<uuid:message_id>/",
        delivery_views.delivery_detail,
        name="delivery",
    ),
    path(
        "mail/sent/<uuid:message_id>/resolution/",
        delivery_views.resolution_command,
        name="delivery_resolve",
    ),
    path(
        "mail/sent/<uuid:message_id>/bounce-report/",
        delivery_views.bounce_report,
        name="delivery_bounce_report",
    ),
    path("mail/refusals/", delivery_views.refusal_list, name="delivery_refusals"),
    path(
        "mail/refusals/<uuid:refusal_id>/",
        delivery_views.refusal_detail,
        name="delivery_refusal",
    ),
    path(
        "mail/refusals/<uuid:refusal_id>/clearance/",
        delivery_views.clear_refusal,
        name="delivery_refusal_clear",
    ),
    path(
        "mail/family-portal/",
        family_maintenance_views.family_portal,
        name="family_portal",
    ),
    path("mail/presence/", presence.active_families, name="presence"),
    # Held emails a restore left undecided at release (#757), linked from
    # Home while any is undecided.
    path(
        "mail/held-emails/",
        restore_review_views.held_emails,
        name="held_emails",
    ),
]

# The JSON formats of the presence read that scripts poll (the header count).
POLLED_PRESENCE = frozenset({"count", "json"})


def presence_reads(request):
    """The polled presence address: only its JSON reads answer here.

    ``?format=count`` (the header) and ``?format=json`` answer; any other
    read would be the page, which lives at ``/admin/mail/presence/``, so it
    is 404 (#864). Any requested JSON format keeps the read here, so the view
    still refuses a bad combination.
    """
    if POLLED_PRESENCE & set(request.GET.getlist("format")):
        return presence.active_families(request)
    raise Http404


polled_patterns = [
    path("presence", presence_reads, name="presence_count"),
]
