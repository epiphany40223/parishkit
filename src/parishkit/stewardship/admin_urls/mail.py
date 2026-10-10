"""Mail and Family portal group URLs: mail controls, progress, history, mail.

Pages end in ``/``; their form actions and status fragment sit under them as
nouns (``resolution/``, ``clearance/``, ``status/``). Pause and resume mail
acts on the current campaign, so its campaign-free route hands the view the
current campaign's id. The header's presence count is a JSON read that a
script polls, so it keeps the ``/admin/presence`` address (decision 8);
only the page moved, and any other read there is 404 (#864).
"""

from django.http import Http404
from django.urls import path

from ..accounts import delivery_control_views, family_maintenance_views, presence
from ..accounts.group_root_views import group_root
from ..jobs import delivery_views, send_history_views, send_progress_views
from ..web.admin_routes import current_campaign

patterns = [
    # The group root opens the first entry the viewer may open now.
    path("mail/", group_root("mail"), name="mail_root"),
    path(
        "mail/controls/",
        current_campaign(delivery_control_views.control),
        name="delivery_control",
    ),
    path(
        "mail/family-progress/",
        send_progress_views.family_email_progress,
        name="family_email_progress",
    ),
    path(
        "mail/family-progress/status/",
        send_progress_views.family_email_progress_status,
        name="family_email_progress_status",
    ),
    path(
        "mail/family-history/",
        send_history_views.family_email_sends,
        name="family_email_sends",
    ),
    path("mail/outgoing/", delivery_views.delivery_list, name="deliveries"),
    path(
        "mail/outgoing/<uuid:message_id>/",
        delivery_views.delivery_detail,
        name="delivery",
    ),
    path(
        "mail/outgoing/<uuid:message_id>/resolution/",
        delivery_views.resolution_command,
        name="delivery_resolve",
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
