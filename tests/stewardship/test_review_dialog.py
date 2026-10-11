"""The shared review dialog of Admin editors (#750; UX conventions, "Review
dialog"): the markup ui-v1.js relies on, and the Users and New and Edit
scheduled email pages that use it.

The behaviour (focus, busy state, refusals, the return to the list) is
checked in real browsers (tests/stewardship/browser/test_review_dialog.py,
test_schedule_list.py) and the server's answers under PostgreSQL
(database/test_user_views_postgresql.py,
test_schedule_entry_postgresql.py).
"""

import re
from pathlib import Path

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.user_changes import plan_changes

from .policy_factory import user

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
DIALOG = "stewardship/components/confirm-dialog.html"


def dialog(**values):
    """The review dialog as a page includes it."""
    return render_to_string(
        DIALOG,
        {
            "review": True,
            "dialog_id": "thing-review",
            "title": "Review thing",
            "apply_url": "/admin/things/",
            "outcome_id": "things-outcome",
            "fallback": "thing-form",
        }
        | values,
    )


def test_the_dialog_is_labelled_by_its_focusable_title():
    """aria-labelledby names the title, which takes focus (tabindex=-1)."""
    text = dialog()
    assert 'aria-labelledby="thing-review-title"' in text
    assert re.search(
        r'<h2 id="thing-review-title" tabindex="-1" data-review-title>', text
    )
    assert "Review thing" in text
    assert "<dialog " in text and "data-review-dialog" in text
    # Not the row-action dialog: no row-action wiring.
    assert "data-confirm-dialog" not in text and "data-confirm-form" not in text


def test_apply_posts_the_signed_preview_and_status_sits_below_the_buttons():
    """Apply and Cancel, then the reserved status line, then the refusal."""
    text = dialog()
    apply = re.search(r"<form ([^>]*)>(.*?)</form>", text, re.S)
    assert 'action="/admin/things/"' in apply.group(1)
    assert 'name="action" value="confirm"' in apply.group(2)
    # The review's own hidden fields (its preview) are added by the script.
    assert 'name="preview"' not in apply.group(2)
    actions = text.index('class="dialog-actions"')
    status = text.index('role="status" data-confirm-status')
    error = text.index('role="alert" data-confirm-error hidden')
    assert text.index("data-review-body") < actions < status < error
    assert "Apply" in text[actions:status] and "Cancel" in text[actions:status]
    # Apply is the ordinary primary button, not the Delete dialog's danger.
    assert "button-danger" not in text


def test_the_end_state_names_the_page_and_its_status_line():
    """A return address for New and Edit; none means a redraw in place."""
    returning = dialog(return_url="/admin/users/")
    assert 'data-review-return="/admin/users/"' in returning
    assert 'data-review-outcome="things-outcome"' in returning
    assert 'data-review-return=""' in dialog()
    assert 'data-applied-at="Applied at {time}."' in returning


def test_the_row_action_dialog_is_unchanged():
    """Without review=True the include is still the Delete dialog (#879)."""
    text = render_to_string(
        DIALOG,
        {
            "dialog_id": "user-delete",
            "form_id": "user-actions",
            "title_many": "Delete {count} users?",
            "body": "Gone.",
            "accept": "Delete",
        },
    )
    assert "data-confirm-dialog" in text and 'data-confirm-form="user-actions"' in text
    assert "data-review-dialog" not in text and "data-review-body" not in text


def test_every_review_form_names_a_review_dialog_on_its_page():
    """A form marked data-review-open names a review dialog its template
    includes, so the script never falls back to a native submission."""
    found = 0
    for path in sorted(TEMPLATES.rglob("*.html")):
        source = path.read_text(encoding="utf-8")
        for name in re.findall(r'<form [^>]*data-review-open="([\w-]+)"', source):
            found += 1
            assert re.search(
                r'confirm-dialog\.html" with review=True dialog_id="'
                + re.escape(name)
                + '"',
                source,
            ), (path.name, name)
    assert found >= 2


def test_the_status_line_keeps_its_space_and_is_polite():
    """role=status, focusable, reserved, outside every in-place region."""
    text = render_to_string(
        "stewardship/components/review-outcome.html", {"outcome_id": "users-outcome"}
    )
    assert re.search(
        r'<p class="review-outcome" id="users-outcome" role="status" '
        r'tabindex="-1" data-review-outcome></p>',
        text,
    )
    css = (TEMPLATES.parents[1] / "static/stewardship/ui-v1.css").read_text()
    rule = re.search(r"\.review-outcome \{([^}]*)\}", css).group(1)
    assert "min-height" in rule and "white-space: nowrap" in rule
    source = (TEMPLATES / "users.html").read_text(encoding="utf-8")
    users = source.index('review-outcome.html" with outcome_id="users-outcome"')
    leaders = source.index('review-outcome.html" with outcome_id="leaders-outcome"')
    regions = [match.start() for match in re.finditer("<section id=", source)]
    ends = [match.start() for match in re.finditer("</section>", source)]
    # Above each table region, outside it, so a redraw keeps the line: the
    # users line above the users table, the import's above the leaders
    # table, where the reader acted.
    assert users < regions[0] and ends[0] < leaders < regions[1]


def review_fragment():
    """New user's review, as ``user_views._review`` draws it."""
    records = [user("admin@example.org", "administrator", "Admin, Ada")]
    (change,), _patch = plan_changes(
        records, [{"email": "new@example.org", "name": "Smith, Ann", "role": "staff"}]
    )
    return render_to_string(
        "stewardship/user-review.html",
        {"change": change, "after_role": "Staff", "preview": "signed"},
    )


def test_the_user_review_is_a_fragment_for_the_dialog():
    """No page, form, heading 1 or second-screen link: the dialog's body."""
    text = review_fragment()
    assert "data-review-answer" in text
    assert 'data-review-done="User added."' in text
    assert re.search(r'name="preview" value="signed" data-review-preview', text)
    assert "<form" not in text and "<h1" not in text and "<html" not in text
    assert "Return to" not in text and "Cancel" not in text
    assert "new@example.org" in text and "Smith, Ann" in text


def test_the_leader_review_offers_apply_only_with_someone_to_import():
    """Nobody to import: no preview, so the dialog shows no Apply."""
    leader = {
        "email": "ann@example.org",
        "name": "Jones, Ann",
        "member_duid": 901,
    }
    some = render_to_string(
        "stewardship/user-leader-import.html",
        {"added": [leader], "skipped": [], "preview": "signed", "limit": 100},
    )
    assert "data-review-answer" in some and "data-review-preview" in some
    assert 'data-review-done="Leaders imported."' in some
    assert "<form" not in some and "<h1" not in some
    none = render_to_string(
        "stewardship/user-leader-import.html",
        {"added": [], "skipped": [], "preview": None, "limit": 100},
    )
    assert "Nobody to import" in none and "data-review-preview" not in none


def test_new_and_edit_user_review_in_the_dialog_and_return_to_users():
    """The entry form opens user-review; Apply posts to Users, then returns."""
    source = (TEMPLATES / "user-entry.html").read_text(encoding="utf-8")
    assert 'id="user-entry-form" data-review-open="user-review"' in source
    assert re.search(
        r'review=True dialog_id="user-review" title=review_title '
        r"apply_url=apply_url return_url=list_url "
        r'outcome_id="users-outcome"',
        source,
    )
    # The leader import stays on Users and redraws in place: no return.
    users = (TEMPLATES / "users.html").read_text(encoding="utf-8")
    include = re.search(r'review=True dialog_id="leader-review"[^%]*%', users).group(0)
    assert "return_url" not in include and 'outcome_id="leaders-outcome"' in include


def test_the_dialog_body_is_a_focusable_region_named_by_the_title():
    """A long review with nothing else to focus can still be scrolled by
    keyboard (axe's scrollable-region-focusable)."""
    assert re.search(
        r'<div class="dialog-body" tabindex="0" role="region" '
        r'aria-labelledby="thing-review-title" data-review-body>',
        dialog(),
    )


def schedule_fragment(changes, *, new=True, blocking=0):
    """New or Edit scheduled email's review, as the view answers it."""
    return render_to_string(
        "stewardship/schedule-review.html",
        {
            "campaign": {"active_configuration": {"name": "Stewardship 2054"}},
            "changes": changes,
            "window_changes": {},
            "blocking": blocking,
            "preview": None if blocking else "signed",
            "new": new,
        },
    )


def scheduled(operation="add", **impact):
    """One schedule change as build_preview describes it (no schedules)."""
    return {
        "label": "Reminder",
        "operation": operation,
        "before": None,
        "after": None,
        "impact": impact,
    }


def test_the_schedule_review_is_a_fragment_for_the_dialog():
    """Headings under the dialog's title, the signed preview, and the line
    the list starts its status with; no page, form or second-screen link."""
    text = schedule_fragment([scheduled()])
    assert "data-review-answer" in text
    assert 'data-review-done="Scheduled email added."' in text
    assert re.search(r'name="preview" value="signed" data-review-preview', text)
    assert "<h3>Reminder — Add</h3>" in text and "<h4>Current schedule</h4>" in text
    assert "<form" not in text and "<h1" not in text and "<h2" not in text
    assert "Return to" not in text and "Apply all changes" not in text
    repeated = schedule_fragment([scheduled() for _ in range(30)])
    assert repeated.count("Reminder — Add") == 30
    assert 'data-review-done="30 reminders added."' in repeated
    edited = schedule_fragment([scheduled("update")], new=False)
    assert 'data-review-done="Scheduled email changed."' in edited


def test_a_blocked_schedule_review_offers_no_apply():
    """Sends in progress block the change: the reason, and no preview."""
    text = schedule_fragment([scheduled("update", blocking=2)], new=False, blocking=2)
    assert "Sends blocking the change: 2" in text
    assert "data-review-preview" not in text


def test_new_and_edit_scheduled_email_review_in_the_dialog():
    """The entry form is gated on a change and opens schedule-review; Apply
    posts to Dates and mail schedules, then returns there to its line."""
    source = (TEMPLATES / "schedule-entry.html").read_text(encoding="utf-8")
    assert re.search(
        r'id="schedule-entry-form" data-schedule-entry '
        r'data-review-open="schedule-review" data-require-change="base_digest"',
        source,
    )
    assert re.search(
        r'review=True dialog_id="schedule-review" title=review_title '
        r"apply_url=apply_url return_url=list_url "
        r'outcome_id="schedules-outcome"',
        source,
    )
    assert "data-unchanged-hint data-idle" in source
    listing = (TEMPLATES / "schedule-settings.html").read_text(encoding="utf-8")
    assert 'review-outcome.html" with outcome_id="schedules-outcome"' in listing
