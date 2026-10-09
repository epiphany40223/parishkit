"""Users and access group URLs: sign-in rules, Ministry assignments, Chairpersons.

Portal users is three pages (decision 13, #535): Sign-in rules (who may sign
in, and with which roles), Ministry assignments (which Ministries a Ministry
leader leads) and Chairpersons (suspended Chairperson assignments and the
parish source's Chairperson suggestions). Each review is a POST-only page
under the page it is started from, so a confirmed change returns there. The
role autosave's JSON endpoints and its status poll sit under Sign-in rules;
the page hands their addresses to its script in ``data-`` attributes. Admin
automation (ADM-11) already followed the scheme.
"""

from django.urls import path

from ..accounts import (
    assignment_views,
    automation_views,
    chair_review_views,
    chair_views,
    rule_autosave_views,
    user_rule_views,
    user_views,
)
from ..accounts.group_root_views import group_root

patterns = [
    # The group root opens the first entry the viewer may open now.
    path("users/", group_root("users"), name="users_root"),
    path("users/sign-in-rules/", user_views.sign_in_rules, name="users"),
    path(
        "users/sign-in-rules/review/",
        user_rule_views.user_rules,
        name="user_rules",
    ),
    path(
        "users/sign-in-rules/autosave/",
        rule_autosave_views.rule_apply,
        name="rule_apply",
    ),
    path(
        "users/sign-in-rules/autosave/base/",
        rule_autosave_views.rule_base,
        name="rule_base",
    ),
    path(
        "users/sign-in-rules/requests/<uuid:request_id>/",
        rule_autosave_views.rule_request,
        name="rule_request",
    ),
    path(
        "users/ministry-assignments/",
        user_views.ministry_assignments,
        name="ministry_assignments",
    ),
    path(
        "users/ministry-assignments/review/",
        assignment_views.assignments,
        name="assignments",
    ),
    path("users/chairpersons/", user_views.chairpersons, name="chairpersons"),
    path(
        "users/chairpersons/suggestions/",
        chair_views.chair_confirmations,
        name="chair_confirmations",
    ),
    path(
        "users/chairpersons/reviews/",
        chair_review_views.chair_reviews,
        name="chair_reviews",
    ),
    # The browser side of the Admin automation command line (ADM-11): nouns,
    # trailing slashes, and actions posted to the collection or item they
    # change.
    path("users/automation/", automation_views.access_view, name="automation_access"),
    path(
        "users/automation/approval/",
        automation_views.approval_view,
        name="automation_approval",
    ),
    path(
        "users/automation/sessions/<uuid:session_id>/",
        automation_views.session_view,
        name="automation_session",
    ),
    path(
        "users/automation/notices/",
        automation_views.notices_view,
        name="automation_notices",
    ),
]
