"""A census change the parish added, in its field on the reviewed form (#2008).

The reviewed form draws a standing parish-added census change (#1710) in
its field, editable, and the browser marks it "Added by the parish" (the
payload's ``added``; review pipeline spec, "What the reviewed form shows").
The form records it as a ``parish`` shown item, so a Family that submits
the value unchanged makes no change of its own (reviewed rule 1), and the
submission check (frozen file 0170,
``schema/migrations/0170_parish_added_shown.sql``) admits that; any other
answer is always a Family change To review, even ParishSoft's value, which
the file's derived guard admits and its submission check requires. These
tests submit real responses on the restricted web login and check the
file's DO block against the bodies it replaces.
"""

import json
import re
from dataclasses import replace

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.responses import census_review, reviewed
from parishkit.stewardship.responses.census_additions import AddRefusal
from parishkit.stewardship.responses.models import (
    CensusAddition,
    CensusAdditionResolution,
    ProposedChange,
    Submission,
)

from .test_census_add_page_postgresql import rows_of, worklist
from .test_census_additions_postgresql import (
    add,
    address_form,
    campaign_of,
    member_form,
)
from .test_census_pipeline_review_postgresql import decide, prepared
from .test_ministry_responses_postgresql import (
    ministry_source,
    respond,
    revisit,
)
from .test_response_http_postgresql import answers_for, post
from .test_reviewed_form_postgresql import (
    again,
    drawn_before_the_release,
    first_name,
    household_field,
    member_field,
)
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare, promote
from .test_strict_phase_order_postgresql import SCHEMA, Rollback, body

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = SCHEMA / "migrations/0170_parish_added_shown.sql"
NAME = "stewardship_submission_effects_v1"
EARLIER = SCHEMA / "migrations/0160_resubmission_phase_order.sql"
DERIVED = "stewardship_response_derived_guard_v1"
DERIVED_EARLIER = SCHEMA / "migrations/0163_edit_replacement.sql"
# The file's three added clauses in the submission check, each from its
# comment to its end: the answers check's two (left as shown, edited) and
# the earlier changes check's.
ANSWERS = re.compile(
    r"\n *-- A change the parish added \(#2008\) that a response drawn as\n"
    r".*?IS NOT DISTINCT FROM submitted_key\)\)",
    re.S,
)
EDITED = re.compile(
    r"\n *-- Under that same 'parish' entry any other answer is an\n"
    r".*?requires a Family change' USING ERRCODE='23514';\n *END IF;",
    re.S,
)
EARLIER_CHANGES = re.compile(
    r"\n *-- An earlier change of a field whose change the parish added\n"
    r".*?THEN\n *CONTINUE;\n *END IF;",
    re.S,
)
# The derived guard's one edit: its unchanged-value refusal, which 0163
# raised for every change whose value is ParishSoft's, spares an edited
# change the parish added (from the comment to the closing parentheses).
UNCHANGED = (
    "            IF (NEW.current_available AND submitted_key"
    " IS NOT DISTINCT FROM current_key)\n"
    "               OR (NOT NEW.current_available AND submitted_key IS NULL)\n"
    "               OR (NEW.baseline_available AND submitted_key"
    " IS NOT DISTINCT FROM baseline_key)\n"
    "            THEN"
)
SPARED = re.compile(
    r" *IF \(\(NEW\.current_available AND submitted_key"
    r".*?IS DISTINCT FROM submitted_key\)\)\n *THEN",
    re.S,
)


def changes(field):
    """The Family's census changes of one field, oldest first."""
    return list(ProposedChange.objects.filter(field=field).order_by("created_at"))


def steps(addition):
    """The later steps recorded on an addition."""
    return list(CensusAdditionResolution.objects.filter(addition=addition))


def parishsoft_nickname(harness, value="Bob"):
    """A refresh after which ParishSoft holds Member 3's nickname ``value``."""
    data = ministry_source()
    data.members[3]["nickName"] = value
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    return value


def set_nickname(value):
    """A change that sets Member 3's nickname in the answers."""

    def apply(answers):
        answers["members"]["3"]["nickname"] = value

    return apply


def test_an_unchanged_submit_makes_no_change(response_service):
    """An open addition is drawn in its field, marked added and changed; sent
    back as drawn it makes no change, and the addition stays open and is
    drawn again on the next form."""
    harness, _, staff, rows = prepared(response_service)
    before = ProposedChange.objects.count()
    addition, _ = add(
        harness, staff, campaign_of(rows), member_form(rows, "nickname", "Bobby")
    )
    form, response = again(harness)
    nickname = member_field(form, "nickname")
    assert (nickname["value"], nickname["added"], nickname["changed"]) == (
        "Bobby",
        True,
        True,
    )
    assert member_field(form, "suffix")["added"] is False
    assert response.answers["members"]["3"]["nickname"] == "Bobby"
    assert [
        entry["shown"]
        for entry in response.shown_items
        if entry["item"] == str(addition.pk)
    ] == ["parish"]
    assert ProposedChange.objects.count() == before
    assert changes("nickname") == []
    assert steps(addition) == []
    form, _ = again(harness)
    assert member_field(form, "nickname")["value"] == "Bobby"
    assert member_field(form, "nickname")["added"] is True
    # The Family's own changes are drawn as before.
    assert member_field(form, "first_name")["value"] == "Requested"
    assert ProposedChange.objects.count() == before


def test_an_edited_value_makes_exactly_one_family_change(response_service):
    """Any other answer is an ordinary Family change To review; the addition
    is left as it is, and the next form shows the Family's newer answer."""
    harness, _, staff, rows = prepared(response_service)
    addition, _ = add(
        harness, staff, campaign_of(rows), member_form(rows, "nickname", "Bobby")
    )
    again(harness, set_nickname("Rob"))
    (change,) = changes("nickname")
    assert (change.submitted_value, change.decision, change.execution) == (
        "Rob",
        "unreviewed",
        "pending",
    )
    assert steps(addition) == []
    form, _ = again(harness)
    assert member_field(form, "nickname")["value"] == "Rob"
    assert member_field(form, "nickname")["added"] is False
    assert len(changes("nickname")) == 1


def test_an_ignored_change_it_replaced_stays_ignored(response_service):
    """Added in place of an Ignored change: the field draws the added value;
    sent back unchanged, the Ignored change stays Ignored and open, no row is
    made, and both items are recorded again on the next form."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["first_name"]], "ignore")
    addition, _ = add(
        harness,
        staff,
        campaign_of(rows),
        member_form(
            rows, "first_name", "Robert", in_place_of=str(rows["first_name"].pk)
        ),
    )
    form, response = again(harness)
    assert member_field(form, "first_name")["value"] == "Robert"
    assert member_field(form, "first_name")["added"] is True
    shown = {
        entry["shown"]
        for entry in response.shown_items
        if entry["field"] == "first_name"
    }
    assert shown == {"baseline", "parish"}
    (kept,) = changes("first_name")
    assert (kept.pk, kept.decision, kept.execution) == (
        rows["first_name"].pk,
        "ignored",
        "pending",
    )
    form, _ = again(harness)
    assert member_field(form, "first_name")["value"] == "Robert"
    assert len(changes("first_name")) == 1
    assert steps(addition) == []


def test_an_edit_replaces_the_ignored_change_it_was_added_for(response_service):
    """Edited instead, the answer is a new change that supersedes the Ignored
    one, as any other changed answer of a shown Ignored change is."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["first_name"]], "ignore")
    add(
        harness,
        staff,
        campaign_of(rows),
        member_form(
            rows, "first_name", "Robert", in_place_of=str(rows["first_name"].pk)
        ),
    )

    def rob(answers):
        answers["members"]["3"]["first_name"] = "Rob"

    again(harness, rob)
    old, new = changes("first_name")
    assert (old.execution, old.superseded_by_id) == ("superseded", new.pk)
    assert (new.submitted_value, new.decision) == ("Rob", "unreviewed")


@pytest.mark.parametrize("field", ["home_address", "mailing_address"])
def test_an_address_addition_submitted_unchanged(response_service, field):
    """A Family address addition fills that address's fields, marked added;
    sent back as drawn it makes no change of either address. For the home
    address it is added in place of the Family's Ignored home address."""
    harness, _, staff, rows = prepared(response_service)
    in_place_of = ""
    if field == "home_address":
        decide(harness, staff, [rows["home_address"]], "ignore")
        in_place_of = str(rows["home_address"].pk)
    fields = address_form(rows, field, line1="5 New St", city="Louisville")
    fields["value_in_place_of"] = [in_place_of]
    add(harness, staff, campaign_of(rows), fields)
    before = {name: changes(name) for name in ("home_address", "mailing_address")}
    form, _ = again(harness)
    drawn = household_field(form, field)
    assert (drawn["value"]["line1"], drawn["added"]) == ("5 New St", True)
    other = "mailing_address" if field == "home_address" else "home_address"
    assert household_field(form, other)["added"] is False
    assert household_field(form, "email_opt_out")["added"] is False
    after = {name: changes(name) for name in ("home_address", "mailing_address")}
    assert [[row.pk for row in rows] for rows in after.values()] == [
        [row.pk for row in rows] for rows in before.values()
    ]
    if field == "home_address":
        (kept,) = after["home_address"]
        assert (kept.decision, kept.execution) == ("ignored", "pending")


def test_mailing_same_as_home_after_a_home_address_addition(response_service):
    """The Family ticks "mailing same as home" under an added home address:
    the home address is left as shown (no change), and the mailing address,
    now that address, is exactly one Family change."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["home_address"]], "ignore")
    fields = address_form(rows, "home_address", line1="5 New St", city="Louisville")
    fields["value_in_place_of"] = [str(rows["home_address"].pk)]
    add(harness, staff, campaign_of(rows), fields)

    def same_as_home(answers):
        answers["family"]["mailing_same_as_home"] = True
        answers["family"]["mailing_address"] = dict(answers["family"]["home_address"])

    again(harness, same_as_home)
    (home,) = changes("home_address")
    assert (home.decision, home.execution) == ("ignored", "pending")
    (mailing,) = changes("mailing_address")
    assert mailing.submitted_value["line1"] == "5 New St"
    assert (mailing.decision, mailing.execution) == ("unreviewed", "pending")


def test_an_addition_withdrawn_while_the_form_is_open_is_a_stale_form(
    response_service,
):
    """Withdrawn (Withdraw) after the form was drawn: the submit is a
    stale-form review, and the fresh form draws the field without it; sent
    as drawn that fresh form makes no change."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    addition, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bobby"))
    with web_login():
        form = revisit(harness)
    answers = answers_for(form)
    assert answers["members"]["3"]["nickname"] == "Bobby"
    worklist(harness, staff, campaign, "withdraw", {f"add-{addition.pk}": 1})
    with web_login():
        before = Submission.objects.count()
        result = post(
            harness.client,
            "/family/submit",
            {"baseline": form["baseline"], "answers": answers},
        )
        assert result.status_code == 409
        assert result.json()["error"] == "review_required"
        fresh = result.json()["form"]
        assert Submission.objects.count() == before
        nickname = member_field(fresh, "nickname")
        assert nickname["value"] != "Bobby" and nickname["added"] is False
        respond(harness, fresh, answers_for(fresh))
    assert changes("nickname") == []


def test_an_addition_entered_while_the_form_is_open_is_left_as_shown(
    response_service,
):
    """Entered by hand after the form was drawn: the submit commits with no
    change, and the addition stays Entered by hand."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    addition, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bobby"))
    with web_login():
        form = revisit(harness)
    worklist(harness, staff, campaign, "entered_by_hand", {f"add-{addition.pk}": 1})
    with web_login():
        respond(harness, form, answers_for(form))
    assert changes("nickname") == []
    assert [step.action for step in steps(addition)] == ["entered"]


def test_an_entered_addition_stands_until_parishsoft_changes(response_service):
    """Entered by hand, the field still draws the added value while
    ParishSoft has the value it had when the change was added; once a
    refresh shows ParishSoft's new value, the field shows ParishSoft's."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    addition, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bobby"))
    worklist(harness, staff, campaign, "entered_by_hand", {f"add-{addition.pk}": 1})
    form, _ = again(harness)
    assert member_field(form, "nickname")["added"] is True
    data = ministry_source()
    data.members[3]["nickName"] = "Bob"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    form, _ = again(harness)
    nickname = member_field(form, "nickname")
    assert (nickname["value"], nickname["added"]) == ("Bob", False)
    assert changes("nickname") == []


def test_a_withdrawn_addition_is_not_drawn(response_service):
    """A withdrawn addition is never drawn: the field shows what it would
    otherwise show."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    withdrawn, _ = add(harness, staff, campaign, member_form(rows, "suffix", "Jr."))
    worklist(harness, staff, campaign, "withdraw", {f"add-{withdrawn.pk}": 1})
    form, _ = again(harness)
    suffix = member_field(form, "suffix")
    assert suffix["value"] != "Jr." and suffix["added"] is False


def test_a_form_not_drawn_as_reviewed_shows_no_addition(response_service):
    """A form drawn by the rule before the reviewed form (as a Testing form
    is) draws the Family's own answers and no addition."""
    harness, _, staff, rows = prepared(response_service)
    add(harness, staff, campaign_of(rows), member_form(rows, "nickname", "Bo"))
    with web_login(), drawn_before_the_release():
        form = revisit(harness)
    nickname = member_field(form, "nickname")
    assert nickname["value"] != "Bo" and nickname["added"] is False


def test_the_add_dialog_says_the_family_will_see_it(response_service, google):
    """Staff's Census changes page draws Add's dialog with the Family sentence."""
    from .test_census_worklist_page_postgresql import ROUTE, staff_browser
    from .test_information_followup_postgresql import search

    prepared(response_service)
    response, body_bytes = search(staff_browser(google), ROUTE, {})
    assert response.status_code == 200
    assert (
        "The Family will see this value on its form, marked Added by the parish, "
        "the next time it signs in."
    ) in body_bytes.decode()


def test_the_open_addition_wins_over_a_later_entered_one(response_service):
    """A Entered, B added and Entered, A reopened: A is the field's only open
    addition, so the field draws A, not the newer B."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    first, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bo"))
    worklist(harness, staff, campaign, "entered_by_hand", {f"add-{first.pk}": 1})
    second, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bobby"))
    worklist(harness, staff, campaign, "entered_by_hand", {f"add-{second.pk}": 1})
    form, _ = again(harness)
    assert member_field(form, "nickname")["value"] == "Bobby"
    worklist(
        harness, staff, campaign, "back_to_review", {f"add-{first.pk}": 2}, note="Redo"
    )
    form, _ = again(harness)
    assert member_field(form, "nickname")["value"] == "Bo"
    assert changes("nickname") == []


def test_a_value_added_again_after_a_withdrawal_draws_the_new_one(response_service):
    """A withdrawn addition and a later one of the same field: the field
    draws the later value."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    first, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bo"))
    worklist(harness, staff, campaign, "withdraw", {f"add-{first.pk}": 1})
    add(harness, staff, campaign, member_form(rows, "nickname", "Bobby"))
    form, _ = again(harness)
    assert member_field(form, "nickname")["value"] == "Bobby"


def test_the_payload_carries_only_the_value_and_the_flag(response_service):
    """With an addition that has a Staff note, the Family's form data and
    page carry the value and the ``added`` flag only: no note, no Staff
    email, no field key more, and not the badge's words."""
    from .test_reviewed_form_postgresql import FIELD_KEYS, HOUSEHOLD_KEYS

    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    note = "Called the office about it"
    add(harness, staff, campaign, member_form(rows, "nickname", "Bobby"), note=note)
    add(harness, staff, campaign, address_form(rows, line1="5 New St"), note=note)
    form, _ = again(harness)
    for entry in form["members"]:
        for field in entry["fields"]:
            assert set(field) == FIELD_KEYS, field["name"]
    for field in form["household"]["fields"]:
        assert set(field) == HOUSEHOLD_KEYS, field["name"]
    text = json.dumps({key: value for key, value in form.items() if key != "content"})
    assert "Bobby" in text and "5 New St" in text
    for word in (note, "staff@example.org", "Added by the parish"):
        assert word not in text, word
    with web_login():
        revisit(harness)
    page = harness.client.get("/family/")
    assert page.status_code == 200
    for word in (note, "staff@example.org"):
        assert word not in page.content.decode(), word


def two_family_source():
    """The Ministry fixture with a second responding Family: Member 5 of 2."""
    data = ministry_source()
    data.families[2] = {
        **data.families[1],
        "familyDUID": 2,
        "familyID": 12,
        "lastName": "Second",
        "eMailAddress": "second@example.org",
    }
    data.members[5] = {
        **data.members[3],
        "memberDUID": 5,
        "familyDUID": 2,
        "firstName": "Other",
        "emailAddress": "other@example.org",
    }
    data.member_contactinfos[5] = {"memberDUID": 5, "middleName": "Other"}
    return data


def second_family(harness):
    """Family 2 of ``two_family_source`` signs in and responds as drawn.

    Returns its harness, so a change can be added for it (census Add needs
    a Family that responded) and its own form drawn.
    """
    family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=2)
    other = replace(
        harness,
        code=harness.rings.general.decrypt(
            family.code_ciphertext, context=code_context(family.pk)
        ).decode(),
    )
    with web_login():
        form = revisit(other)
        respond(other, form, answers_for(form))
    return other


def test_a_family_never_sees_another_familys_addition(response_service, monkeypatch):
    """Two Families, a parish-added change on each: each reviewed form draws
    only its own value, and the other's is nowhere in its form data."""
    from . import test_ministry_responses_postgresql as ministry

    monkeypatch.setattr(ministry, "ministry_source", two_family_source)
    harness, _, staff, rows = prepared(response_service)
    other = second_family(harness)
    campaign = campaign_of(rows)
    add(harness, staff, campaign, member_form(rows, "nickname", "Bobby"))
    add(
        harness,
        staff,
        campaign,
        {
            "value_field": ["nickname"],
            "value_family": ["2"],
            "value_member": ["5"],
            "value_nickname": ["Fivey"],
            "value_in_place_of": [""],
        },
    )
    for who, own, members, theirs in (
        (harness, "Bobby", "3", "Fivey"),
        (other, "Fivey", "5", "Bobby"),
    ):
        form, _ = again(who)
        assert [entry["id"] for entry in form["members"]] == [members]
        assert member_field(form, "nickname", members)["value"] == own
        assert member_field(form, "nickname", members)["added"] is True
        assert theirs not in json.dumps(form)
    assert changes("nickname") == []


def test_an_edit_back_to_parishsoft_is_a_change_to_review(response_service):
    """The Family turns the added value down by typing ParishSoft's value
    back: that is a Family change To review all the same (decision 2), with
    ParishSoft's value, and the addition is left as it is; the next form
    shows the Family's answer, not the added value."""
    harness, _, staff, rows = prepared(response_service)
    parishsoft = parishsoft_nickname(harness)
    addition, _ = add(
        harness, staff, campaign_of(rows), member_form(rows, "nickname", "Bobby")
    )
    again(harness, set_nickname(parishsoft))
    (change,) = changes("nickname")
    assert (change.submitted_value, change.decision, change.execution) == (
        parishsoft,
        "unreviewed",
        "pending",
    )
    assert change.baseline_value == parishsoft
    assert steps(addition) == []
    form, _ = again(harness)
    nickname = member_field(form, "nickname")
    assert (nickname["value"], nickname["added"]) == (parishsoft, False)
    assert changes("nickname") == [change]


def test_an_edit_to_the_ignored_value_replaces_the_ignored_change(response_service):
    """Added in place of an Ignored change, whose form value is ParishSoft's
    first name: the Family typing that value back makes a new change To
    review that replaces the Ignored one, not a quiet no-change."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["first_name"]], "ignore")
    add(
        harness,
        staff,
        campaign_of(rows),
        member_form(
            rows, "first_name", "Robert", in_place_of=str(rows["first_name"].pk)
        ),
    )
    parishsoft = rows["first_name"].baseline_value
    again(harness, first_name(parishsoft))
    old, new = changes("first_name")
    assert (old.decision, old.execution, old.superseded_by_id) == (
        "ignored",
        "superseded",
        new.pk,
    )
    assert (new.submitted_value, new.decision, new.execution) == (
        parishsoft,
        "unreviewed",
        "pending",
    )


def test_a_newer_confirmed_change_wins_over_an_older_addition(response_service):
    """Bobby added; the Family edits it to Rob, Staff accept it, and it ends
    Confirmed with Rob in ParishSoft. A Confirmed change records no shown
    item, yet it is newer than the open addition, so the form shows
    ParishSoft's Rob, not the parish's Bobby with its badge."""
    harness, _, staff, rows = prepared(response_service)
    add(harness, staff, campaign_of(rows), member_form(rows, "nickname", "Bobby"))
    again(harness, set_nickname("Rob"))
    (rob,) = changes("nickname")
    decide(harness, staff, [rob], "accept")
    parishsoft_nickname(harness, "Rob")
    form, _ = again(harness)
    nickname = member_field(form, "nickname")
    assert (nickname["value"], nickname["added"]) == ("Rob", False)
    # Stands in for the update run and the refresh that confirms it: the
    # change ends Confirmed with ParishSoft's value, as the reviewer's
    # reproduction left it.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        ProposedChange.objects.filter(pk=rob.pk).update(
            execution="resolved_upstream", version=F("version") + 1
        )
    form, _ = again(harness)
    nickname = member_field(form, "nickname")
    assert (nickname["value"], nickname["added"], nickname["changed"]) == (
        "Rob",
        False,
        False,
    )
    assert changes("nickname") == [rob]


def test_an_entered_addition_keeps_the_change_it_replaced_ignored(response_service):
    """Once the addition made in place of an Ignored change is Entered by
    hand, the Ignored change still cannot go back to review: the form draws
    the addition over it, and an unchanged submit would lose its answer.
    The census page withholds its Back to review, and a post is refused."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    decide(harness, staff, [rows["first_name"]], "ignore")
    ignored = ProposedChange.objects.get(pk=rows["first_name"].pk)
    addition, _ = add(
        harness,
        staff,
        campaign,
        member_form(rows, "first_name", "Robert", in_place_of=str(ignored.pk)),
    )
    worklist(harness, staff, campaign, "entered_by_hand", {f"add-{addition.pk}": 1})
    _, shown = rows_of(harness, staff, campaign)
    assert shown[str(ignored.pk)]["covered"] is True
    with pytest.raises(AddRefusal) as refused:
        worklist(
            harness,
            staff,
            campaign,
            "back_to_review",
            {str(ignored.pk): ignored.version},
            note="Family meant it",
        )
    assert refused.value.code == "reopen_added"
    ignored.refresh_from_db()
    assert (ignored.decision, ignored.execution) == ("ignored", "pending")


def test_a_change_newer_than_an_entered_addition_can_go_back(response_service):
    """An Entered by hand addition covers only the changes made before it:
    a later Family change of the field, Ignored, goes back to review."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    addition, _ = add(harness, staff, campaign, member_form(rows, "nickname", "Bo"))
    worklist(harness, staff, campaign, "entered_by_hand", {f"add-{addition.pk}": 1})
    again(harness, set_nickname("Rob"))
    (rob,) = changes("nickname")
    decide(harness, staff, [rob], "ignore")
    rob.refresh_from_db()
    _, shown = rows_of(harness, staff, campaign)
    assert shown[str(rob.pk)]["covered"] is False
    worklist(
        harness,
        staff,
        campaign,
        "back_to_review",
        {str(rob.pk): rob.version},
        note="Family meant it",
    )
    rob.refresh_from_db()
    assert rob.decision == "unreviewed"


def test_an_open_older_change_is_drawn_instead_of_the_addition(
    response_service, monkeypatch
):
    """The reviewer's lost change, on a change reopened under an Entered by
    hand addition before Back to review refused that: the form draws the
    reopened change, not the addition, so an unchanged submit keeps it and a
    later edit replaces it, leaving one open change of the field."""
    harness, _, staff, rows = prepared(response_service)
    campaign = campaign_of(rows)
    decide(harness, staff, [rows["first_name"]], "ignore")
    ignored = ProposedChange.objects.get(pk=rows["first_name"].pk)
    addition, _ = add(
        harness,
        staff,
        campaign,
        member_form(rows, "first_name", "Robert", in_place_of=str(ignored.pk)),
    )
    worklist(harness, staff, campaign, "entered_by_hand", {f"add-{addition.pk}": 1})
    monkeypatch.setattr(census_review, "covered_by_addition", lambda proposal: False)
    worklist(
        harness,
        staff,
        campaign,
        "back_to_review",
        {str(ignored.pk): ignored.version},
        note="Family meant it",
    )
    form, _ = again(harness)
    drawn = member_field(form, "first_name")
    assert (drawn["value"], drawn["added"]) == ("Requested", False)
    assert changes("first_name") == [ignored]
    again(harness, first_name("Rob"))
    old, new = changes("first_name")
    assert (old.execution, old.superseded_by_id) == ("superseded", new.pk)
    assert [
        row.submitted_value
        for row in changes("first_name")
        if row.execution in ("pending", "conflict")
    ] == ["Rob"]


# --- The frozen file -------------------------------------------------------


def test_the_earlier_check_refuses_an_unchanged_submit(response_service):
    """On 0160's submission check, the same unchanged submit after an
    addition is refused, as the bug this file fixes."""
    harness, _, staff, rows = prepared(response_service)
    add(harness, staff, campaign_of(rows), member_form(rows, "nickname", "Bobby"))
    with connection.cursor() as cursor:
        cursor.execute(body(NAME, EARLIER))
    try:
        with pytest.raises(
            DatabaseError, match="Submitted census change requires atomic derived work"
        ):
            again(harness)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(body(NAME, FROZEN))
    assert changes("nickname") == []


def test_the_second_clause_admits_the_ignored_change_left_beside_it(
    response_service,
):
    """Without the file's second clause (the earlier changes), an Ignored
    change left beside the added value that replaced it is refused as
    unreconciled earlier work: both clauses are needed."""
    harness, _, staff, rows = prepared(response_service)
    decide(harness, staff, [rows["home_address"]], "ignore")
    fields = address_form(rows, "home_address", line1="5 New St")
    fields["value_in_place_of"] = [str(rows["home_address"].pk)]
    add(harness, staff, campaign_of(rows), fields)
    (second,) = EARLIER_CHANGES.findall(body(NAME, FROZEN))
    with connection.cursor() as cursor:
        cursor.execute(body(NAME, FROZEN).replace(second, "", 1))
    try:
        with pytest.raises(
            DatabaseError, match="New census response must reconcile earlier census"
        ):
            again(harness)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(body(NAME, FROZEN))


def test_the_added_value_with_no_parish_item_is_a_change(response_service):
    """The new clauses admit the added value only through the form's own
    ``parish`` entry: a response whose form recorded none (a form drawn by
    the earlier rule) sending that value makes its change, as before."""
    harness, _, staff, rows = prepared(response_service)
    add(harness, staff, campaign_of(rows), member_form(rows, "nickname", "Bobby"))
    with drawn_before_the_release():
        again(harness, set_nickname("Bobby"))
    (change,) = changes("nickname")
    assert change.submitted_value == "Bobby"


def test_the_earlier_derived_guard_refuses_an_edit_back(response_service):
    """On 0163's derived guard, the change an edit back to ParishSoft's
    value makes is refused as unchanged: the file's derived guard edit is
    what admits it."""
    harness, _, staff, rows = prepared(response_service)
    parishsoft = parishsoft_nickname(harness)
    add(harness, staff, campaign_of(rows), member_form(rows, "nickname", "Bobby"))
    with connection.cursor() as cursor:
        cursor.execute(body(DERIVED, DERIVED_EARLIER))
    try:
        with pytest.raises(
            DatabaseError, match="Unchanged values cannot create actionable proposals"
        ):
            again(harness, set_nickname(parishsoft))
    finally:
        with connection.cursor() as cursor:
            cursor.execute(body(DERIVED, FROZEN))
    assert changes("nickname") == []


def test_the_submission_check_requires_the_edited_change(response_service, monkeypatch):
    """Python made to leave an edit back to ParishSoft's value with no row
    (as before decision 2) is refused by the submission check: under the
    form's 'parish' entry any other answer needs its Family change."""
    harness, _, staff, rows = prepared(response_service)
    parishsoft = parishsoft_nickname(harness)
    add(harness, staff, campaign_of(rows), member_form(rows, "nickname", "Bobby"))
    monkeypatch.setattr(reviewed, "parish_left_as_shown", lambda *args: True)
    with pytest.raises(
        DatabaseError,
        match="An edited census change the parish added requires a Family change",
    ):
        again(harness, set_nickname(parishsoft))
    assert changes("nickname") == []


def forged(monkeypatch, **changed):
    """Draw the reviewed form's 'parish' entries with ``changed`` keys.

    A hand-built baseline, as a forged form would carry: Python draws and
    trusts it (``parish_left_as_shown`` checks only the value), so only the
    submission check stands between it and an unrecorded change.
    """
    real = reviewed.parish_entries

    def entries(inputs, family, answered):
        """The real entries, each with ``changed`` applied."""
        return [entry | changed for entry in real(inputs, family, answered)]

    monkeypatch.setattr(reviewed, "parish_entries", entries)


def test_the_check_refuses_another_familys_addition(response_service, monkeypatch):
    """A 'parish' entry naming another Family's addition, its value sent
    back as drawn, is refused: the check binds the addition to this Family
    (and to the answer's own Member)."""
    from . import test_ministry_responses_postgresql as ministry

    monkeypatch.setattr(ministry, "ministry_source", two_family_source)
    harness, _, staff, rows = prepared(response_service)
    second_family(harness)
    campaign = campaign_of(rows)
    add(harness, staff, campaign, member_form(rows, "nickname", "Bobby"))
    theirs, _ = add(
        harness,
        staff,
        campaign,
        {
            "value_field": ["nickname"],
            "value_family": ["2"],
            "value_member": ["5"],
            "value_nickname": ["Fivey"],
            "value_in_place_of": [""],
        },
    )
    forged(monkeypatch, item=str(theirs.pk))
    with pytest.raises(
        DatabaseError, match="Submitted census change requires atomic derived work"
    ):
        again(harness)
    assert changes("nickname") == []


def test_the_check_refuses_this_familys_addition_for_another_field(
    response_service, monkeypatch
):
    """A 'parish' entry naming this Family's nickname addition under the
    middle name, its value sent back as drawn there, is refused: the check
    binds the addition to the answer's own field."""
    harness, _, staff, rows = prepared(response_service)
    add(harness, staff, campaign_of(rows), member_form(rows, "nickname", "Bobby"))
    forged(monkeypatch, field="middle_name")
    with pytest.raises(
        DatabaseError, match="Submitted census change requires atomic derived work"
    ):
        again(harness)
    assert changes("middle_name") == []


def test_the_do_block_refuses_the_earlier_bodies():
    """The file's closing check passes as installed and raises once either
    replaced function is put back to the body it replaced; on those bodies
    the whole file installs again, with no definer."""
    text = FROZEN.read_text(encoding="utf-8")
    captured = text[
        text.index("SELECT set_config(") : text.index("CREATE OR REPLACE FUNCTION")
    ]
    check = text[text.index("DO $check$") :]
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(captured)
        cursor.execute(check)
        raise Rollback
    for name, earlier, refusal in (
        (NAME, EARLIER, "does not admit a change the parish added"),
        (DERIVED, DERIVED_EARLIER, "does not admit an edited change the parish"),
    ):
        with (
            pytest.raises(DatabaseError, match=refusal),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(captured)
            cursor.execute(body(name, earlier))
            cursor.execute(check)
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(body(NAME, EARLIER))
        cursor.execute(body(DERIVED, DERIVED_EARLIER))
        cursor.execute(text)
        cursor.execute(
            "SELECT count(*) FROM pg_proc WHERE NOT prosecdef AND proname = ANY(%s) "
            "AND prosrc LIKE %s",
            [[NAME, DERIVED], "%shown->>'shown'='parish'%"],
        )
        assert cursor.fetchone() == (2,)
        raise Rollback


def test_the_file_adds_only_the_parish_clauses():
    """Each replaced body is its earlier one with only the #2008 edits:
    removing the submission check's three added clauses (each from its
    comment to its end) gives 0160's body back exactly, comments included,
    and putting the derived guard's unchanged-value refusal back as it was
    gives 0163's; the file makes no definer."""
    new = body(NAME, FROZEN)
    added = [
        *ANSWERS.findall(new),
        *EDITED.findall(new),
        *EARLIER_CHANGES.findall(new),
    ]
    assert len(added) == 3
    for clause in added:
        new = new.replace(clause, "", 1)
    assert new == body(NAME, EARLIER)
    derived = body(DERIVED, FROZEN)
    (spared,) = SPARED.findall(derived)
    assert derived.replace(spared, UNCHANGED, 1) == body(DERIVED, DERIVED_EARLIER)
    text = FROZEN.read_text(encoding="utf-8")
    assert not re.search(r"^\s*[^-\s].*SECURITY DEFINER", text, re.M)
    assert CensusAddition._meta.db_table in text
