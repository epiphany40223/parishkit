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
from django.urls import reverse

from parishkit.stewardship.accounts.user_changes import plan_changes
from parishkit.stewardship.accounts.user_views import review_lines

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


# A button partial whose dialog its page includes instead: System health's
# action buttons (#1356), drawn in the region and in the polled copy alike,
# while the dialogs sit outside every region on the page.
DIALOG_PAGES = {
    "system-health-backup-button.html": "system-health.html",
    "system-health-debug-button.html": "system-health.html",
    # Finish switching on an integration's status line (#1568).
    "integration-summary.html": "integration-settings.html",
}


def test_every_review_form_names_a_review_dialog_on_its_page():
    """A form marked data-review-open names a review dialog its template
    (or, for a button partial, its page) includes, so the script never falls
    back to a native submission."""
    found = 0
    for path in sorted(TEMPLATES.rglob("*.html")):
        source = path.read_text(encoding="utf-8")
        if path.name in DIALOG_PAGES:
            source = (TEMPLATES / DIALOG_PAGES[path.name]).read_text(encoding="utf-8")
        for name in re.findall(
            r'<form [^>]*data-review-open="([\w-]+)"', path.read_text(encoding="utf-8")
        ):
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
    # The shared rule, not Campaign images' slot lines (.artwork-slot, #1410).
    rule = re.search(r"^\.review-outcome \{([^}]*)\}", css, re.M).group(1)
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
        {"change": change, "lines": [], "preview": "signed"},
    )


def test_the_user_review_is_a_fragment_for_the_dialog():
    """No page, form, heading 1 or second-screen link: the dialog's body."""
    text = review_fragment()
    assert "data-review-answer" in text
    assert 'data-review-done="User added."' in text
    assert re.search(r'name="preview" value="signed" data-review-preview', text)
    assert "<form" not in text and "<h1" not in text and "<html" not in text
    assert "Return to" not in text and "Cancel" not in text


def test_new_user_confirms_without_repeating_the_form():
    """New user's Review is a confirmation (#1356): one sentence the dialog
    names as its description, and none of the form's values; making the
    person an Administrator keeps its warning."""
    text = review_fragment()
    assert '<p id="user-review-summary">This adds the user' in text
    assert "new@example.org" not in text and "Smith, Ann" not in text
    assert "Staff" not in text and "(none)" not in text and "<table" not in text
    records = [user("admin@example.org", "administrator", "Admin, Ada")]
    (change,), _patch = plan_changes(
        records,
        [{"email": "new@example.org", "name": "Smith, Ann", "role": "administrator"}],
    )
    granted = render_to_string(
        "stewardship/user-review.html", {"change": change, "preview": "signed"}
    )
    assert "This makes the person an Administrator" in granted
    source = (TEMPLATES / "user-entry.html").read_text(encoding="utf-8")
    assert 'translate "Add the user" %}{% endbutton %}' in source
    assert 'translate "Add this user?" as review_title' in source
    assert 'describedby="user-review-summary" %}' in source


def test_edit_user_is_a_compact_review_of_each_changed_field():
    """Edit user (class A2, #1356): one "Field: old → new" line per changed
    field, the lines describing the dialog; no table and no email heading.
    The Administrator removal warning stays, and an unchanged field has no
    line."""
    records = [user(), user("ann@example.org", "administrator", "Smith, Ann")]
    (change,), _patch = plan_changes(
        records, [{"id": records[1]["id"], "name": "Jones, Ann", "role": "staff"}]
    )
    assert review_lines(change) == [
        {"label": "Name", "before": "Smith, Ann", "after": "Jones, Ann"},
        {"label": "Role", "before": "Administrator", "after": "Staff"},
    ]
    text = render_to_string(
        "stewardship/user-review.html",
        {"change": change, "lines": review_lines(change), "preview": "signed"},
    )
    assert '<ul class="compact-review" id="user-review-summary">' in text
    assert "Smith, Ann" in text and "Jones, Ann" in text
    assert "<table" not in text and "<h3" not in text and "ann@example.org" not in text
    assert "no longer an Administrator" in text
    assert "Nothing changes until" not in text
    (renamed,), _patch = plan_changes(
        records,
        [{"id": records[1]["id"], "name": "Jones, Ann", "role": "administrator"}],
    )
    assert [line["label"] for line in review_lines(renamed)] == ["Name"]
    source = (TEMPLATES / "user-entry.html").read_text(encoding="utf-8")
    assert 'translate "Save" %}{% endbutton %}' in source
    assert 'translate "Save this user change?" as review_title' in source
    assert 'describedby="user-review-summary" %}' in source


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


def test_importing_every_ticked_leader_is_a_confirmation():
    """Import selected with nobody skipped or left over (#1356): the
    question and sentence the dialog takes, the signed preview, and none of
    the ticked rows."""
    leaders = [
        {"email": f"{name}@example.org", "name": name, "member_duid": 900 + number}
        for number, name in enumerate(("ann", "bob"))
    ]
    text = render_to_string(
        "stewardship/user-leader-import.html",
        {
            "added": leaders,
            "skipped": [],
            "preview": "signed",
            "limit": 100,
            "confirmation": True,
        },
    )
    assert 'data-review-title="Import the selected leaders?"' in text
    assert 'data-review-describedby="leader-import-summary"' in text
    assert (
        '<p id="leader-import-summary">This adds the 2 chosen leaders as users '
        "with the role Ministry leader.</p>" in text
    )
    assert 'name="preview" value="signed" data-review-preview' in text
    assert "ann@example.org" not in text and "<table" not in text


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


def test_a_one_time_new_schedule_confirms_without_repeating_the_form():
    """New scheduled email adding one schedule that sends once is a
    confirmation (#1356): it names its own question and sentence, which the
    dialog takes for this answer, and lists no schedule or counts. A Repeat,
    a recurring schedule or an Edit is not."""
    from parishkit.stewardship.accounts.schedule_entry_views import _sends_once

    once = scheduled() | {"after": {"date": "2054-10-15"}}
    reviewed = {
        "changes": [once],
        "window_changes": {},
        "series": [],
        "blocking": 0,
        "preview": "signed",
    }
    assert _sends_once(reviewed, None)
    assert not _sends_once(reviewed | {"changes": [once, once]}, None)
    recurring = scheduled() | {"after": {"date": None}}
    assert not _sends_once(reviewed | {"changes": [recurring]}, None)
    assert not _sends_once(
        reviewed | {"changes": [once | {"operation": "update"}]}, None
    )
    assert not _sends_once(reviewed | {"window_changes": {"end_date": 1}}, None)
    assert not _sends_once(reviewed | {"series": [{"id": "x"}]}, None)
    text = render_to_string(
        "stewardship/schedule-review.html",
        {"changes": [once], "preview": "signed", "new": True, "confirmation": True},
    )
    assert re.search(
        r'<div data-review-answer data-review-done="Scheduled email added." '
        r'data-review-title="Add this scheduled email\?" '
        r'data-review-describedby="schedule-review-summary">',
        text,
    )
    assert '<p id="schedule-review-summary">This schedules the email' in text
    assert 'name="preview" value="signed" data-review-preview' in text
    assert "Current schedule" not in text and "Reminder — Add" not in text
    source = (TEMPLATES / "schedule-entry.html").read_text(encoding="utf-8")
    assert 'translate "Add the scheduled email" %}' in source


def test_only_a_schedule_that_has_sent_says_sent_email_is_not_recalled():
    """The not-recalled notice shows only when build_preview counts email
    the changed schedules already sent (R40b): never for a future send."""
    notice = "Email already sent cannot be recalled."
    assert notice not in schedule_fragment([scheduled("update")], new=False)
    sent = render_to_string(
        "stewardship/schedule-review.html",
        {
            "changes": [scheduled("update", delivered=3)],
            "delivered": 3,
            "preview": "signed",
        },
    )
    assert f'<p class="notice">{notice}</p>' in sent


def test_delete_marks_only_a_schedule_that_has_sent_for_its_caution():
    """Delete scheduled email's caution is for sent email only (R40b): a
    row's Delete is marked data-confirm-caution only with sent email, and
    the dialog's caution starts hidden for ui-v1.js to show for marked
    rows. Without caution_marked, a dialog's caution is always said."""
    from django.template import Context, Template

    actions = Template(
        "{% load stewardship %}{% table_actions name='Reminder 1' delete='x' "
        "confirm='schedule-delete' field='schedule_id' caution=sent %}"
    )
    assert "data-confirm-caution" in actions.render(Context({"sent": 4}))
    assert "data-confirm-caution" not in actions.render(Context({"sent": 0}))
    marked = render_to_string(
        DIALOG,
        {"dialog_id": "schedule-delete", "caution": "Never.", "caution_marked": True},
    )
    assert '<p class="notice" data-confirm-caution-marked hidden>Never.</p>' in marked
    plain = render_to_string(DIALOG, {"dialog_id": "x", "caution": "Never."})
    assert '<p class="notice">Never.</p>' in plain
    source = (TEMPLATES / "schedule-settings.html").read_text(encoding="utf-8")
    assert "{% if row.delivered %} data-confirm-caution{% endif %}" in source
    assert "caution=row.delivered %}" in source


def test_a_blocked_schedule_review_offers_no_apply():
    """Sends in progress block the change: the reason, and no preview."""
    text = schedule_fragment([scheduled("update", blocking=2)], new=False, blocking=2)
    assert "Sends blocking the change: 2" in text
    assert "data-review-preview" not in text
    # The page the message names is linked (navigation rule 6, #521).
    assert f'<a href="{reverse("admin:deliveries")}">Sent mail</a>' in text


def test_new_and_edit_scheduled_email_review_in_the_dialog():
    """The entry form is gated on a change and opens schedule-review; Apply
    posts to Dates and mail schedules, then returns there to its line.

    Inside the campaign wizard the form saves directly instead (#1404 PR
    4b), with the same change gate.
    """
    source = (TEMPLATES / "schedule-entry.html").read_text(encoding="utf-8")
    assert re.search(
        r'id="schedule-entry-form" data-schedule-entry \{% if direct %\}'
        r"data-direct-save [^\n]*?"
        r'\{% else %\}data-review-open="schedule-review"\{% endif %\} '
        r'data-require-change="base_digest"',
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


def test_a_confirmation_is_described_by_its_sentence():
    """A confirmation (#1356) names its one-sentence summary as the dialog's
    description, and a page whose own address cannot be read once the change
    applies redraws from Apply's answer; an ordinary review does neither."""
    text = dialog(describedby="thing-summary", redraw_answer=True)
    assert 'aria-describedby="thing-summary"' in text
    assert 'data-review-redraw="answer"' in text
    plain = dialog()
    assert "aria-describedby" not in plain and "data-review-redraw" not in plain


def test_create_the_campaign_confirms_without_repeating_the_form():
    """Create the campaign's Review is a confirmation (#1356): its form's
    button carries the action's label, and its fragment holds the summary the
    dialog names, the server's page-and-email count and the signed preview,
    and no changed values."""
    page = (TEMPLATES / "campaign-create.html").read_text()
    assert 'data-review-open="campaign-create-review"' in page
    assert 'describedby="campaign-create-summary"' in page
    assert "redraw_answer=True" in page
    assert 'translate "Review changes"' not in page
    assert 'translate "Create the campaign and continue" %}{% endbutton %}' in page
    fragment = render_to_string(
        "stewardship/campaign-create-review.html",
        {"review": {"content": 1, "preview": "signed", "next_url": "/next/"}},
    )
    assert re.search(r'<div data-review-answer data-review-done="[^"]+"', fragment)
    # Once applied, the dialog opens the next Campaign setup step (#1404).
    assert (
        'data-review-return="/next/" data-review-return-outcome="campaign-step-outcome"'
        in fragment
    )
    assert '<p id="campaign-create-summary">' in fragment
    assert "It starts with 1 page or email in the default text." in fragment
    assert 'name="preview" value="signed" data-review-preview' in fragment
    assert "Current" not in fragment and "Proposed" not in fragment


def artwork_fragment(removing):
    """Campaign images' review fragment: a removal, or a staged upload."""
    from types import SimpleNamespace
    from uuid import UUID

    asset = SimpleNamespace(pk=UUID(int=1), label="section", width=256, height=256)
    return render_to_string(
        "stewardship/artwork-review.html",
        {
            "review": {
                "removing": removing,
                "slot": "welcome",
                "bundle": UUID(int=2),
                "asset": None if removing else asset,
                "label": "Welcome page icon",
                "preview": "signed",
            }
        },
    )


def test_removing_a_campaign_image_is_a_confirmation():
    """Remove this image is a confirmation (#1356): the answer names its own
    question and its one sentence, which describes the dialog, and drops the
    fixed "nothing changes" line. An upload keeps the dialog's own title,
    since the staged image is new (class C)."""
    removal = artwork_fragment(True)
    assert 'data-review-title="Remove this image?"' in removal
    assert 'data-review-describedby="artwork-review-summary"' in removal
    assert (
        '<p id="artwork-review-summary">Welcome page icon: Families will see no'
        " image there until you upload a new one.</p>"
    ) in removal
    assert "Nothing changes until" not in removal
    assert 'name="preview" value="signed" data-review-preview' in removal
    upload = artwork_fragment(False)
    assert "data-review-title" not in upload and "artwork-review-summary" not in upload
    assert "not yet in use" in upload


def test_apply_may_post_another_action_and_a_drawn_refusal_is_shown():
    """Apply posts "confirm" unless the page names another action
    (apply_action, System health's steps, #1356), and a refusal the server
    draws (data-review-refusal) is shown as drawn, step-up included."""
    assert 'name="action" value="confirm"' in dialog()
    named = dialog(apply_action="debug_off_confirm")
    assert 'name="action" value="debug_off_confirm"' in named
    script = (TEMPLATES.parents[1] / "static/stewardship/ui-v1.js").read_text()
    assert '.querySelector("[data-review-refusal]")' in script
    assert "refusalMarkup(answer.text)\n        || refusalText(" in script
    # Review, Apply and a key confirmation's Apply (#1568).
    assert script.count("refusalMarkup(answer.text)") == 3
    # Redrawn from Apply's answer itself, without reading the page again.
    assert 'data-review-redraw="body"' in dialog(redraw_body=True)
    assert 'dialog.dataset.reviewRedraw === "body"' in script


def test_the_compact_review_draws_one_line_per_changed_field():
    """A compact review (#1356, class A2): "Field: old → new" per change, the
    arrow hidden from screen readers, which hear "old, becomes new"; a line
    with no old value draws the new one alone; values are escaped."""
    text = render_to_string(
        "stewardship/components/compact-review.html",
        {
            "summary_id": "thing-summary",
            "changes": [
                {"label": "Role", "before": "Staff", "after": "Ministry leader"},
                {"label": "Added", "before": None, "after": "<b>Stock</b>"},
            ],
        },
    )
    assert text.startswith('<ul class="compact-review" id="thing-summary">')
    assert (
        '<li><span class="compact-review-field">Role:</span> Staff '
        '<span aria-hidden="true">→</span><span class="visually-hidden">, '
        "becomes</span> Ministry leader</li>"
    ) in text
    assert (
        '<li><span class="compact-review-field">Added:</span> '
        "&lt;b&gt;Stock&lt;/b&gt;</li>"
    ) in text
    assert text.count("<li>") == 2 and "<table" not in text


def share_option(identity, label, free_text=False):
    """One saved share option, as the campaign's values hold it."""
    return {"id": identity, "label": label, "free_text": free_text}


def test_share_options_mark_only_what_changed():
    """Share options' compact review (#1356) has one line per real change,
    in the new order then the removed options: an addition, a relabel, a
    free-text change and a move, but no line for the options the move or
    the removal only shifted."""
    from parishkit.stewardship.accounts.option_editing import option_changes

    before = [
        share_option("a", "Check"),
        share_option("b", "Online"),
        share_option("c", "Stock"),
        share_option("d", "IRA"),
        share_option("e", "Other", True),
        share_option("f", "Cash"),
    ]
    after = [
        share_option("d", "IRA gift"),
        share_option("a", "Check"),
        share_option("c", "Stock"),
        share_option("e", "Other"),
        share_option("f", "Cash"),
        share_option("n", "Envelopes", True),
    ]
    assert [
        (str(line["label"]), line["before"], str(line["after"]))
        for line in option_changes(before, after)
    ] == [
        ("Label", "IRA", "IRA gift"),
        ("Position of “IRA gift”", "4", "1"),
        ("Free-text response for “Other”", "Yes", "No"),
        ("Added", None, "Envelopes (with a free-text response)"),
        ("Removed", None, "Online"),
    ]
    assert option_changes(before, before) == []
    # Deleting the first option shifts every other one, but moves none.
    assert [str(line["label"]) for line in option_changes(before, before[1:])] == [
        "Removed"
    ]
    text = render_to_string(
        "stewardship/share-options-review.html",
        {"review": {"changes": option_changes(before, after), "preview": "signed"}},
    )
    assert '<ul class="compact-review" id="share-review-summary">' in text
    assert text.count("<li>") == 5 and "<ol>" not in text
    assert 'name="preview" value="signed" data-review-preview' in text
    page = (TEMPLATES / "share-settings.html").read_text()
    assert 'describedby="share-review-summary" %}' in page


def test_share_option_reorders_name_the_first_longest_run_as_kept():
    """Ties between equally long kept runs go to the first found (#1356):
    swapping two neighbours names only the later-listed one as moved, and
    swapping two blocks names the block that now comes last."""
    from parishkit.stewardship.accounts.option_editing import option_changes

    def moves(order, new_order):
        """The review's lines, as (label, before, after), for a reorder."""
        options = {key: share_option(key, key.upper()) for key in order}
        return [
            (str(line["label"]), line["before"], str(line["after"]))
            for line in option_changes(
                [options[key] for key in order], [options[key] for key in new_order]
            )
        ]

    assert moves("abc", "bac") == [("Position of “A”", "1", "2")]
    assert moves("abcd", "cdab") == [
        ("Position of “A”", "1", "3"),
        ("Position of “B”", "2", "4"),
    ]


def test_a_small_campaign_settings_change_is_a_compact_review():
    """Campaign settings (#1356): one or two short settings changed is a
    compact review, which names its own question and description and keeps
    the change's notes; more settings, or a long one, keep the full table
    under the dialog's own title."""
    from parishkit.stewardship.accounts.campaign_views import _compact

    assert _compact({"name": "Renamed"})
    assert _compact({"name": "Renamed", "modules": ["census"]})
    assert not _compact({"name": "A", "modules": ["census"], "end_date": "x"})
    for key in ("ministry_duids", "financial", "share_options"):
        assert not _compact({key: []})
    changes = [{"label": "Campaign name", "before": "Old", "after": "New"}]

    def fragment(compact, notes=()):
        """The review fragment as campaign_views._review draws it."""
        return render_to_string(
            "stewardship/campaign-settings-review.html",
            {
                "review": {
                    "changes": changes,
                    "notes": list(notes),
                    "compact": compact,
                    "preview": "signed",
                }
            },
        )

    compact = fragment(True, ["Share options go."])
    assert 'data-review-title="Save this change?"' in compact
    assert 'data-review-describedby="campaign-review-summary"' in compact
    assert '<ul class="compact-review" id="campaign-review-summary">' in compact
    assert "<table" not in compact and "Nothing changes until" not in compact
    assert '<p class="notice" role="note">Share options go.</p>' in compact
    assert 'name="preview" value="signed" data-review-preview' in compact
    full = fragment(False)
    assert "data-review-title" not in full and "campaign-review-summary" not in full
    assert "<table" in full and "Nothing changes until you choose Apply." in full


def test_parish_settings_is_a_compact_review():
    """Parish settings (#1356 PR 5): every setting is short, so every change
    is a compact review, one "Setting: old → new" line each, described by
    its lines and asked as "Save the Parish settings?", keeping a new
    timezone's note; an unset online giving address reads "Not set", never
    an empty side."""
    from parishkit.stewardship.accounts.parish_views import TIMEZONE_NOTE, _shown

    assert str(_shown("online_giving_url", "")) == "Not set"
    assert _shown("name", "Sample Parish") == "Sample Parish"
    text = render_to_string(
        "stewardship/parish-settings-review.html",
        {
            "review": {
                "changes": [
                    {"label": "Online giving", "before": "Not set", "after": "x"},
                    {"label": "Parish timezone", "before": "A", "after": "B"},
                ],
                "notes": [TIMEZONE_NOTE],
                "preview": "signed",
            }
        },
    )
    assert '<ul class="compact-review" id="parish-review-summary">' in text
    assert text.count("<li>") == 2 and "<h3>" not in text
    assert "Current:" not in text and "Nothing changes until" not in text
    assert f'<p class="notice" role="note">{TIMEZONE_NOTE}</p>' in text
    assert 'name="preview" value="signed" data-review-preview' in text
    page = (TEMPLATES / "parish-settings.html").read_text()
    assert '"Save the Parish settings?" as review_title' in page
    assert 'describedby="parish-review-summary" %}' in page
    assert "label=save_label" in page and "Review Parish settings" not in page


def test_the_page_reads_a_pasted_key_as_the_server_does():
    """ui-v1.js's SERVER_SPACE, which decides whether an integration's key
    field holds a key (data-review-key, #750, #1568), is exactly the set of
    characters Python's str.strip() removes (str.isspace): the server and
    the key field's cleaning use str.strip(), so the page reviews the key's
    confirmation exactly when the server takes the key-save path. JavaScript's
    own trim() differs (U+FEFF, U+0085, U+001C-U+001F)."""
    script = (TEMPLATES.parents[1] / "static/stewardship/ui-v1.js").read_text()
    (body,) = re.findall(r"const SERVER_SPACE = /\^\[(.+?)\]\*\$/;", script)
    space = re.compile(f"[{body}]")
    for code in range(0x110000):
        character = chr(code)
        assert bool(space.fullmatch(character)) == character.isspace(), hex(code)
    assert "\N{ZERO WIDTH NO-BREAK SPACE}".strip() and not "\x85".strip()


def _key_confirmation(target, **values):
    """An integration's key confirmation (#1568), as the dialog receives it."""
    from html import unescape

    return unescape(
        render_to_string(
            "stewardship/integration-review.html",
            {"kind": "key", "target": target, "label": target, "changes": []} | values,
        )
    )


def test_a_pasted_key_is_a_confirmation_without_any_field():
    """Save with a key pasted asks its own question and says what happens
    next; it has no hidden field (no preview: Apply posts the form itself),
    and only ParishSoft's and Slack's sentences name the key's last
    characters, which the page script fills in from the field (#1568)."""
    for target, question, tail in (
        ("parishsoft", "Replace the ParishSoft key?", True),
        ("google_workspace", "Replace the Google Workspace key?", False),
        ("slack", "Replace the Slack bot token?", True),
    ):
        text = _key_confirmation(target)
        assert f'data-review-title="{question}"' in text
        assert "data-review-key " in text and "<input" not in text
        assert ("{tail}" in text) is tail
        assert "This usually takes a minute." in text
    setup = _key_confirmation("slack", setup=True)
    assert 'data-review-title="Set up Slack?"' in setup
    assert "Slack is set up once the check passes" in setup
    script = (TEMPLATES.parents[1] / "static/stewardship/ui-v1.js").read_text()
    # The key is left out of the Review, and Apply posts the form itself.
    assert "data.delete(form.dataset.reviewKey);" in script
    assert 'data.append("key_review", "yes");' in script
    assert "new URLSearchParams(new FormData(form))" in script
    assert "reviewNative" not in script


def test_finish_switching_is_a_confirmation_carrying_its_key_change():
    """Finish switching's confirmation (#1568) says the integration is
    stopped, lists the settings saved with the key and carries the signed
    preview and the key change's id that Apply posts."""
    from html import unescape

    text = unescape(
        render_to_string(
            "stewardship/integration-review.html",
            {
                "kind": "finish",
                "target": "parishsoft",
                "label": "ParishSoft",
                "changes": [
                    {
                        "label": "Expected ParishSoft organization ID",
                        "before": "12345",
                        "after": "54321",
                    }
                ],
                "preview": "signed",
                "request_id": "0b9e1c4e-8a8e-4f43-9a51-3f6c1d2e7a10",
            },
        )
    )
    assert 'data-review-title="Finish switching to the new key?"' in text
    assert "ParishSoft is stopped until you finish switching" in text
    assert "Settings saved with the new key" in text
    assert re.findall(r'<input type="hidden" name="(\w+)"', text) == [
        "preview",
        "request_id",
    ]
    summary = (TEMPLATES / "integration-summary.html").read_text()
    assert 'data-review-region="integration-editor"' in summary
    assert "key-changes" not in summary and "Key replacement status" not in summary
