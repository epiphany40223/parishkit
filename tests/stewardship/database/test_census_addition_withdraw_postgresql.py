"""A parish-added census change's own actions and its withdrawal on Accept (#2041).

An addition is withdrawn, not sent back to review: Withdraw while To enter
by hand, and once Entered by hand Not entered yet (the mark's undo) and
Withdraw (both steps, with the undo's required note). Accepting the
Family's own change of the same field (Accept, Edit, in bulk, or the
service the command line uses) withdraws its open addition in the same
transaction, so one field never has two values waiting to be entered;
Ignore leaves it standing. No schema change: the existing ``reopened`` and
``withdrawn`` steps under the frozen 0147 guard.
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.census_additions import (
    AddRefusal,
    resolve_census_additions,
)
from parishkit.stewardship.responses.census_review import (
    WITHDRAWN_BY_ACCEPT,
    review_census_change,
)
from parishkit.stewardship.responses.models import open_additions

from .test_background_grants_postgresql import task_login
from .test_census_add_page_postgresql import rows_of, worklist
from .test_census_additions_postgresql import add, campaign_of, member_form
from .test_census_pipeline_review_postgresql import decide, prepared
from .test_census_review_postgresql import fresh
from .test_reviewed_form_additions_postgresql import (
    again,
    changes,
    set_nickname,
    steps,
)
from .test_reviewed_form_postgresql import member_field

pytestmark = pytest.mark.django_db(transaction=True)


def added_then_asked(response_service, *, entered=False):
    """The parish adds Member 3's nickname "Dave" (Entered by hand when
    ``entered``), then the Family submits "Davey"; (harness, staff,
    campaign, rows, addition, the Family's change)."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    addition, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Dave"))
    if entered:
        worklist(harness, staff, campaign, "entered_by_hand", {f"add-{addition.pk}": 1})
    again(harness, set_nickname("Davey"))
    (asked,) = changes("nickname")
    return harness, staff, campaign, rows, addition, asked


def actions(addition):
    """The addition's steps, in order, as (action, note)."""
    return [(step.action, step.note) for step in sorted(steps(addition), key=sequence)]


def sequence(step):
    """A step's place in its addition's history."""
    return step.sequence


def test_accept_withdraws_the_open_addition_of_its_field(response_service):
    """The Family's change says Accept withdraws the addition; Accept does,
    under the reviewer's name with its own note and audit event, and the
    next form draws the Family's answer without the badge."""
    harness, staff, campaign, _, addition, asked = added_then_asked(response_service)
    _, shown = rows_of(harness, staff, campaign)
    assert shown[str(asked.pk)]["withdraws"] == "Nickname (Dave)"
    assert shown[f"add-{addition.pk}"]["overlap"] is True
    items = {str(asked.pk): asked.version}
    notice = worklist(harness, staff, campaign, "accept", items)
    assert notice.endswith("accepted.")
    (step,) = steps(addition)
    assert (step.action, step.note, step.actor_id) == (
        "withdrawn",
        WITHDRAWN_BY_ACCEPT,
        staff,
    )
    event = AuditEvent.objects.get(subject_id=step.pk)
    assert event.event_type == "census_change_updated"
    assert not open_additions().filter(pk=addition.pk).exists()
    _, history = rows_of(harness, staff, campaign, "history")
    assert history[f"add-{addition.pk}"]["stage"] == "history"
    form, _ = again(harness)
    nickname = member_field(form, "nickname")
    assert (nickname["value"], nickname["added"]) == ("Davey", False)


def test_a_replayed_accept_records_no_second_withdrawal(response_service):
    """The same request key again replays the review and withdraws nothing
    more."""
    harness, staff, _, _, addition, asked = added_then_asked(response_service)
    key = uuid4()
    decide(harness, staff, [asked], "accept", key=key)
    decide(harness, staff, [asked], "accept", key=key)
    assert actions(addition) == [("withdrawn", WITHDRAWN_BY_ACCEPT)]


def test_a_bulk_accept_withdraws_the_addition_all_or_none(response_service):
    """A bulk Accept over the Family's nickname and first name changes
    withdraws the nickname's addition in the same transaction."""
    harness, staff, campaign, rows, addition, asked = added_then_asked(response_service)
    first = fresh(rows["first_name"])
    items = {str(asked.pk): asked.version, str(first.pk): first.version}
    assert worklist(harness, staff, campaign, "accept", items) == (
        "2 changes accepted."
    )
    assert actions(addition) == [("withdrawn", WITHDRAWN_BY_ACCEPT)]


def test_accept_edited_withdraws_the_addition(response_service):
    """Edit is Accept with a value: it withdraws the addition too."""
    harness, staff, _, _, addition, asked = added_then_asked(response_service)
    decide(harness, staff, [asked], "edit", value="David")
    assert actions(addition) == [("withdrawn", WITHDRAWN_BY_ACCEPT)]


def test_the_services_approval_withdraws_it_too(response_service):
    """``review_census_change`` (``pk-admin census review``'s service)
    approving the Family's change withdraws the addition as the page does."""
    harness, staff, _, _, addition, asked = added_then_asked(response_service)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        review_census_change(
            harness.service.store,
            staff,
            asked.pk,
            expected_version=asked.version,
            request_key=uuid4(),
            decision="approved",
            note="",
        )
    assert actions(addition) == [("withdrawn", WITHDRAWN_BY_ACCEPT)]


def test_ignore_leaves_the_addition_standing(response_service):
    """Ignoring the Family's change keeps the addition To enter by hand, and
    the form draws it again."""
    harness, staff, campaign, _, addition, asked = added_then_asked(response_service)
    worklist(harness, staff, campaign, "ignore", {str(asked.pk): asked.version})
    assert steps(addition) == []
    assert open_additions().filter(pk=addition.pk).exists()
    form, _ = again(harness)
    nickname = member_field(form, "nickname")
    assert (nickname["value"], nickname["added"]) == ("Dave", True)


def test_accept_leaves_an_entered_addition_as_it_is(response_service):
    """An Entered by hand addition waits for nothing: accepting the Family's
    change leaves it Entered by hand. Its Withdraw is refused while the
    Family's change is open, in plain words, and nothing is recorded."""
    harness, staff, campaign, _, addition, asked = added_then_asked(
        response_service, entered=True
    )
    _, shown = rows_of(harness, staff, campaign)
    assert shown[str(asked.pk)]["withdraws"] == ""
    with pytest.raises(AddRefusal) as refused:
        worklist(
            harness, staff, campaign, "withdraw", {f"add-{addition.pk}": 2}, note="x"
        )
    assert refused.value.code == "reopen_open_change"
    decide(harness, staff, [asked], "accept")
    assert actions(addition) == [("entered", "")]


def test_withdraw_takes_an_open_addition_back(response_service):
    """Withdraw on To enter by hand needs no note: History, and the Family's
    form no longer draws it. Back to review is no choice on it."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    addition, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Dave"))
    with pytest.raises(ValueError, match="does not offer"):
        worklist(harness, staff, campaign, "back_to_review", {f"add-{addition.pk}": 1})
    notice = worklist(harness, staff, campaign, "withdraw", {f"add-{addition.pk}": 1})
    assert notice.startswith("Nickname for Member DUID ")
    assert notice.endswith(" withdrawn.")
    assert actions(addition) == [("withdrawn", "")]
    form, _ = again(harness)
    assert member_field(form, "nickname")["added"] is False


def test_not_entered_yet_undoes_the_mark_with_a_note(response_service):
    """Not entered yet (Back to review) on an entered addition needs a note,
    named in the refusal as the page names the action; with one it is To
    enter by hand again."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    addition, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Dave"))
    worklist(harness, staff, campaign, "entered_by_hand", {f"add-{addition.pk}": 1})
    items = {f"add-{addition.pk}": 2}
    with pytest.raises(AddRefusal) as refused:
        worklist(harness, staff, campaign, "back_to_review", items)
    assert refused.value.code == "note_required"
    assert refused.value.fix == "Write a note, then choose Not entered yet again."
    notice = worklist(harness, staff, campaign, "back_to_review", items, note="Typo")
    assert notice.endswith("marked not entered yet.")
    _, shown = rows_of(harness, staff, campaign)
    assert shown[f"add-{addition.pk}"]["stage"] == "to_enter_by_hand"
    assert actions(addition) == [("entered", ""), ("reopened", "Typo")]


def test_withdraw_after_entered_records_both_steps_once(response_service):
    """Withdraw on an entered addition needs the undo's note, records
    ``reopened`` then ``withdrawn`` with it, each with its own audit event,
    and a replay of the same key records nothing more."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    addition, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Dave"))
    worklist(harness, staff, campaign, "entered_by_hand", {f"add-{addition.pk}": 1})
    with pytest.raises(AddRefusal) as refused:
        worklist(harness, staff, campaign, "withdraw", {f"add-{addition.pk}": 2})
    assert refused.value.fix == "Write a note, then choose Withdraw again."
    assert actions(addition) == [("entered", "")]
    key = uuid4()
    for _ in range(2):
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            (last,) = resolve_census_additions(
                harness.service.store,
                staff,
                campaign,
                {addition.pk: 2},
                "withdraw",
                note="Not needed",
                request_key=key,
            )
        assert (last.action, last.sequence) == ("withdrawn", 3)
    assert actions(addition) == [
        ("entered", ""),
        ("reopened", "Not needed"),
        ("withdrawn", "Not needed"),
    ]
    for step in steps(addition):
        assert AuditEvent.objects.filter(subject_id=step.pk).count() == 1
    _, history = rows_of(harness, staff, campaign, "history")
    assert history[f"add-{addition.pk}"]["stage"] == "history"


def test_a_familys_change_takes_no_withdraw(response_service):
    """Withdraw is an addition's own: posted on the Family's change it is
    refused and nothing is recorded."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    first = rows["first_name"]
    with pytest.raises(ValueError, match="does not offer"):
        worklist(harness, staff, campaign, "withdraw", {str(first.pk): first.version})
