"""The Timeline's review lines, worded from plain history rows (#1774).

``timeline_reviews.steps`` replays each item's stored history and words
each step from the state before and after it; these tests drive it with
the rows ``_HISTORY`` returns, so no database is needed. The PostgreSQL
test (``database/test_timeline_reviews_postgresql.py``) checks the
statement against rows the real review services store.
"""

from datetime import timedelta
from uuid import uuid4

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports import timeline_pdf
from parishkit.stewardship.reports.timeline_reviews import (
    ASKED,
    SHARE_LABELS,
    SHARE_SHORTS,
    WORDS,
    requests,
    steps,
)

from .test_response_metrics import START

MINUTE = timedelta(minutes=1)


def row(stream, item, minute, a=None, b=None, c=None, d=None, **values):
    """One ``_HISTORY`` row; a census change of Member 3's email by default."""
    return {
        "stream": stream,
        "item": item,
        "step": minute,
        "at": START + minute * MINUTE,
        "actor": "staff@example.org",
        "entity_kind": "member",
        "entity_key": "3",
        "subject": "email",
        "a": a,
        "b": b,
        "c": c,
        "d": d,
        "seed_decision": None,
        "seed_set": None,
        "seed_value": None,
        "note": None,
        "answer_first": '"Anne"',
        "answer_last": '"Smith"',
        "source_first": "Ann",
        "source_last": "Smyth",
    } | values


def words(rows, names=None):
    """Each line's What happened, in order."""
    return [str(what) for _at, what, _detail in steps(rows, names)]


def test_census_reviews_and_resolutions_read_as_pipeline_actions():
    """Each review or resolution row is worded from the change's state before it."""
    rows = [
        row("census_review", "p", 1, "approved", "false", None, "false"),
        row("census_review", "p", 2, "approved", "true", '"a@x.org"', "false"),
        row("census_review", "p", 3, "approved", "true", '"b@x.org"', "false"),
        row("census_review", "p", 4, "approved", "false", None, "false"),
        row("census_review", "p", 5, "unreviewed", "false", None, "false"),
        row("census_review", "p", 6, "ignored", "false", None, "false"),
        row("census_resolution", "p", 7, "reopened"),
        row("census_review", "p", 8, "approved", "true", '"c@x.org"', "false"),
        # Use the Family's value anyway keeps the decision accepted.
        row("census_review", "p", 9, "approved", "true", '"c@x.org"', "true"),
        row("census_review", "p", 10, "approved", "false", None, "true"),
        # Another change, entered by hand, ignored, then reopened.
        row("census_resolution", "q", 10, "entered", entity_kind="family"),
        row("census_resolution", "r", 11, "ignored", entity_kind="family"),
        row("census_resolution", "r", 12, "reopened", entity_kind="family"),
    ]
    assert words(rows) == [
        "Accepted",
        "Edited",
        "Edited",
        "Edit cleared",
        "Back to review",
        "Ignored",
        "Back to review",
        "Accepted (edited)",
        "Accepted (edited)",
        "Accepted",
        "Entered by hand",
        "Ignored",
        "Back to review",
    ]
    lines = steps(rows)
    # Details name the field and whom it is about, as the Family answered,
    # and who acted; never a value or a note.
    assert lines[0][2] == "Email address for Smith, Anne; by staff@example.org"
    assert lines[10][2] == "Email address for the Family; by staff@example.org"
    assert all("@x.org" not in detail for _at, _what, detail in lines)
    assert [at for at, _what, _detail in lines] == [row["at"] for row in rows]


def test_undoing_a_terminal_census_step_reads_back_to_review():
    """Back to review of a mark or an update (#1840) reads "Back to review"
    and keeps the decision behind it, so the next step words from it: a
    one-step Entered by hand leaves the change Accepted, and marking it
    again reads Entered by hand."""
    rows = [
        # Accepted, marked, the mark undone, marked again.
        row("census_review", "p", 1, "approved", "false", None, "false"),
        row("census_resolution", "p", 2, "entered"),
        row("census_resolution", "p", 3, "reopened"),
        row("census_resolution", "p", 4, "entered"),
        # Accepted and marked in one step, undone, then sent back again.
        row("census_resolution", "q", 5, "entered"),
        row("census_resolution", "q", 6, "reopened"),
        row("census_review", "q", 7, "unreviewed", "false", None, "false"),
        # An Updated in ParishSoft change (no step of its own here) undone.
        row("census_review", "r", 8, "approved", "false", None, "false"),
        row("census_resolution", "r", 9, "reopened"),
    ]
    assert words(rows) == [
        "Accepted",
        "Entered by hand",
        "Back to review",
        "Entered by hand",
        "Entered by hand",
        "Back to review",
        "Back to review",
        "Accepted",
        "Back to review",
    ]


def test_member_names_fall_back_to_parishsoft_and_additions_say_so():
    """A Member with no answered name reads as ParishSoft has them; a new
    Member's change names the addition; an unknown actor is said so."""
    (line,) = steps(
        [
            row(
                "census_review",
                "p",
                1,
                "approved",
                "false",
                answer_first=None,
                answer_last="null",
                actor=None,
            )
        ]
    )
    assert line[2] == "Email address for Smyth, Ann; by Former portal user"
    (line,) = steps(
        [
            row(
                "census_review",
                "p",
                1,
                "approved",
                "false",
                subject="new_member",
                entity_kind="proposed_member",
                source_first=None,
                source_last=None,
            )
        ]
    )
    assert line[2] == "New Member: Smith, Anne; by staff@example.org"


def test_a_carried_over_change_starts_from_its_predecessors_decision():
    """A resubmission's change that kept its predecessor's decision and
    Reviewer's value (``_HISTORY``'s seed) is replayed from them, so its
    first step reads as what the reviewer did, not as a fresh decision."""
    accepted = {"seed_decision": "approved", "seed_set": "false", "seed_value": None}
    edited = {"seed_decision": "approved", "seed_set": "true", "seed_value": '"a"'}
    ignored = {"seed_decision": "ignored", "seed_set": "false", "seed_value": None}
    rows = [
        row("census_review", "p", 1, "unreviewed", "false", **accepted),
        row("census_review", "q", 2, "approved", "true", '"b"', "false", **edited),
        row("census_resolution", "r", 3, "reopened", **ignored),
        # Nothing carried (an unreviewed predecessor reads the same).
        row("census_review", "s", 4, "approved", "false", seed_decision="unreviewed"),
    ]
    assert words(rows) == ["Back to review", "Edited", "Back to review", "Accepted"]


def test_a_census_review_after_a_silent_reset_names_its_decision():
    """Reconciliation may reset a decision with no history row; the next
    review still reads as the decision it records, never as no change."""
    rows = [
        row("census_review", "p", 1, "approved", "false", None, "false"),
        row("census_review", "p", 2, "approved", "false", None, "false"),
        row("census_review", "q", 3, "unreviewed", "false", None, "false"),
    ]
    assert words(rows) == ["Accepted", "Accepted", "Back to review"]


def test_a_ministry_the_catalog_lost_is_named_by_its_duid():
    """A Ministry the current catalog no longer has reads as the shared
    fallback, never as a gap in the sentence."""
    (line,) = steps(
        [row("ministry_revision", "m", 1, "in_progress", None, "join", subject="9")]
    )
    assert line[2] == "Join Ministry 9 for Smith, Anne; by staff@example.org"


def test_ministry_follow_up_and_roster_ticks_share_one_request():
    """Revisions and ticks of one request replay together, in time order.

    A tick cleared without a revision (before #1840) is its own line. Back
    to review of Entered by hand (#1840) is one line: its revision keeps the
    decision and clears the tick, whose clearing row ``_HISTORY`` leaves
    out, so ticking again reads Entered by hand. Back to review of a
    request the refresh resolved (no decision behind it, In progress with
    the required note) reads Back to review too.
    """
    ministry = {"subject": "9"}
    rows = [
        row("ministry_revision", "m", 1, "in_progress", None, "join", **ministry),
        row("ministry_revision", "m", 2, "resolved", "joined", "join", **ministry),
        row("ministry_roster", "m", 3, "true", None, "join", **ministry),
        row("ministry_roster", "m", 4, "false", None, "join", **ministry),
        row("ministry_revision", "m", 5, "in_progress", None, "join", **ministry),
        row("ministry_revision", "m", 6, "resolved", "declined", "join", **ministry),
        row(
            "ministry_revision",
            "n",
            7,
            "closed_no_response",
            "no_response",
            "leave",
            **ministry,
        ),
        row("ministry_revision", "p", 8, "resolved", "joined", "join", **ministry),
        row("ministry_roster", "p", 9, "true", None, "join", **ministry),
        row(
            "ministry_revision",
            "p",
            10,
            "resolved",
            "joined",
            "join",
            "true",
            **ministry,
        ),
        row("ministry_roster", "p", 11, "true", None, "join", **ministry),
        row(
            "ministry_revision",
            "q",
            12,
            "in_progress",
            None,
            "join",
            "true",
            **ministry,
        ),
    ]
    assert words(rows, {9: "Choir"}) == [
        "Follow-up updated",
        "Accepted",
        "Entered by hand",
        "Entered by hand cleared",
        "Back to review",
        "Ignored",
        "Ignored",
        "Accepted",
        "Entered by hand",
        "Back to review",
        "Entered by hand",
        "Back to review",
    ]
    details = [detail for _at, _what, detail in steps(rows, {9: "Choir"})]
    assert details[0] == "Join Choir for Smith, Anne; by staff@example.org"
    assert details[1] == (
        "Join Choir for Smith, Anne; Joined the Ministry; by staff@example.org"
    )
    assert details[2] == "Join Choir for Smith, Anne; by staff@example.org"
    assert details[6] == (
        "Leave Choir for Smith, Anne; No response; by staff@example.org"
    )


def test_additional_information_follow_up_reads_as_handled():
    """Needed is Accepted (To handle), followed up is Handled, neither is To review.

    Leaving Handled with the Accept kept is Handled's undo, Back to review
    (#1840), as leaving it for To review is.
    """
    item = {"entity_kind": None, "entity_key": None, "subject": None}
    rows = [
        row("information", "i", 1, "true", "false", **item),
        row("information", "i", 2, "true", "true", **item),
        row("information", "i", 3, "true", "false", **item),
        row("information", "i", 4, "true", "false", **item),
        row("information", "i", 5, "false", "false", **item),
        row("information", "i", 6, "false", "true", **item),
    ]
    assert words(rows) == [
        "Accepted",
        "Handled",
        "Back to review",
        "Follow-up updated",
        "Back to review",
        "Handled",
    ]
    assert steps(rows)[0][2] == "Additional information; by staff@example.org"


def test_every_word_has_the_pdf_reviews_marker():
    """The PDF's copy of the words matches the lines' own (#1774)."""
    assert timeline_pdf.REVIEWS == WORDS
    assert {timeline_pdf.kind_of(word) for word in WORDS} == {"review"}
    # No review word is another kind's, so no earlier branch takes it.
    others = (
        timeline_pdf.VISITS
        | timeline_pdf.PROGRESS
        | timeline_pdf.SUBMITTED
        | timeline_pdf.EMAILS
    )
    assert not WORDS & others


def reader(*roles, ministries=()):
    """A portal principal with ``roles`` (and a led Ministry scope)."""
    return Principal(uuid4(), frozenset(roles), frozenset(ministries))


def details(rows, who):
    """Each line's Details, as ``who`` reads them."""
    return [detail for _at, _what, detail in steps(rows, reader=who)]


def test_back_to_review_lines_show_their_note_to_the_streams_readers():
    """A Back to review line ends with its note for a reader of that
    stream's work page (#1893); a decision line never shows its note, and a
    blank note shows nothing."""
    by = "; by staff@example.org"
    rows = [
        row("census_review", "p", 1, "approved", "false", None, "false", note="Ok"),
        row("census_review", "p", 2, "unreviewed", "false", None, "false", note="Why"),
        row("census_resolution", "q", 3, "entered", entity_kind="family"),
        row("census_resolution", "q", 4, "reopened", entity_kind="family", note=" "),
        row("census_resolution", "q", 5, "entered", entity_kind="family"),
        row("census_resolution", "q", 6, "reopened", entity_kind="family", note="X"),
    ]
    assert words(rows) == [
        "Accepted",
        "Back to review",
        "Entered by hand",
        "Back to review",
        "Entered by hand",
        "Back to review",
    ]
    field, family = "Email address for Smith, Anne", "Email address for the Family"
    for who in (reader("staff"), reader("administrator")):
        assert details(rows, who) == [
            field + by,
            field + by + "; note: Why",
            family + by,
            family + by,
            family + by,
            family + by + "; note: X",
        ]
    # A reader who may not open Census changes, and no reader at all, see
    # the lines without a note.
    for who in (reader("ministry_leader", ministries=[9]), None):
        assert not any("note:" in detail for detail in details(rows, who))


def test_a_ministry_note_follows_the_requests_ministry_scope():
    """A Ministry line's note is for the readers of that request's
    Ministry on Ministry requests: Staff, and a leader of that Ministry
    only, never a leader of another."""
    ministry = {"subject": "9"}
    rows = [
        row("ministry_revision", "m", 1, "resolved", "joined", "join", **ministry),
        row("ministry_roster", "m", 2, "true", None, "join", **ministry),
        row(
            "ministry_revision",
            "m",
            3,
            "resolved",
            "joined",
            "join",
            "true",
            note="Ticked the wrong one",
            **ministry,
        ),
    ]
    assert words(rows)[-1] == "Back to review"
    shown = "; by staff@example.org; note: Ticked the wrong one"
    for who in (reader("staff"), reader("ministry_leader", ministries=[9])):
        assert details(rows, who)[-1].endswith(shown)
    assert "note:" not in details(rows, reader("ministry_leader", ministries=[8]))[-1]


def test_an_information_line_shows_only_the_notes_its_step_changed():
    """Additional information revisions store the whole Staff notes, so a
    Back to review line shows them only when its step changed them."""
    item = {"entity_kind": None, "subject": None}
    rows = [
        row("information", "i", 1, "true", "true", note="Called them", **item),
        # Handled undone with a new note, then needed cleared, notes kept.
        row("information", "i", 2, "true", "false", note="Not done yet", **item),
        row("information", "i", 3, "false", "false", note="Not done yet", **item),
    ]
    assert words(rows) == ["Handled", "Back to review", "Back to review"]
    line = "Additional information; by staff@example.org"
    assert details(rows, reader("staff")) == [
        line,
        line + "; note: Not done yet",
        line,
    ]


def test_pledge_lines_read_as_their_action_for_financial_readers():
    """Each pledge review reads as its own action (#2071), an Edit with the
    Reviewer's value as Pledges shows it; a Back to review line shows its
    note to a reader of Pledges, and a reader without financial detail (or
    none) sees no pledge line at all."""
    pledge = {"entity_kind": None, "entity_key": None, "subject": None}
    rows = [
        row("pledge", "7", 1, "accept", "accepted", note="Fine", **pledge),
        row("pledge", "7", 2, "back_to_review", None, note="Typo", **pledge),
        row(
            "pledge",
            "7",
            3,
            "edit",
            "accepted_edited",
            "1300.00",
            "quarterly",
            **pledge,
        ),
        row(
            "pledge",
            "7",
            4,
            "entered_by_hand",
            "accepted_edited",
            "1300.00",
            "quarterly",
            **pledge,
        ),
        # Undoing the mark keeps the edited decision; then an Ignore after
        # the Family changed its pledge, and a $0.00 Edit with no frequency.
        row(
            "pledge",
            "7",
            5,
            "back_to_review",
            "accepted_edited",
            "1300.00",
            "quarterly",
            note="Wrong",
            **pledge,
        ),
        row("pledge", "7", 6, "ignore", "ignored", **pledge),
        row("pledge", "7", 7, "back_to_review", None, **pledge),
        row("pledge", "7", 8, "edit", "accepted_edited", "0.00", None, **pledge),
    ]
    by = "; by staff@example.org"
    for who in (reader("staff"), reader("administrator")):
        lines = steps(rows, reader=who)
        assert [str(what) for _at, what, _detail in lines] == [
            "Accepted",
            "Back to review",
            "Accepted (edited)",
            "Entered by hand",
            "Back to review",
            "Ignored",
            "Back to review",
            "Accepted (edited)",
        ]
        assert details(rows, who) == [
            "Pledge" + by,
            "Pledge" + by + "; note: Typo",
            "Pledge; Reviewer's value: $1,300.00, Quarterly" + by,
            "Pledge" + by,
            "Pledge" + by + "; note: Wrong",
            "Pledge" + by,
            "Pledge" + by,
            "Pledge; Reviewer's value: $0.00, No frequency" + by,
        ]
    census = row("census_review", "p", 9, "approved", "false", None, "false")
    for who in (reader("ministry_leader", ministries=[9]), None):
        assert [detail for _at, _w, detail in steps([*rows, census], reader=who)] == [
            "Email address for Smith, Anne" + by
        ]


def test_an_edits_share_methods_are_named_on_its_line():
    """An Edit that changed the share methods (#2177) names them by label,
    in the configured order, with any Other text; one unknown reads as an
    unavailable method."""
    first, second, gone = (str(uuid4()) for _ in range(3))
    edit = row(
        "pledge",
        "7",
        1,
        "edit",
        "accepted_edited",
        "1300.00",
        "quarterly",
        entity_kind=None,
        entity_key=None,
        subject=f'{{"{gone}": "", "{second}": "Bonds", "{first}": ""}}',
    )
    names = {SHARE_LABELS: {first: "Bank check", second: "Other"}}
    (line,) = steps([edit], names, reader=reader("staff"))
    assert line[2] == (
        "Pledge; Reviewer's value: $1,300.00, Quarterly; share methods: Bank "
        "check, Other: Bonds, Unavailable share method; by staff@example.org"
    )


def test_an_edits_share_methods_are_short_only_in_the_pages_markup():
    """The line's text, which the PDF and ``pk-admin`` show, names a share
    method by its full label; only the page's markup (``Event.cell``) names
    it by its short label, with the full label as tooltip (#2198)."""
    from parishkit.stewardship.reports.timelines import Event

    first, second = str(uuid4()), str(uuid4())
    edit = row(
        "pledge",
        "7",
        1,
        "edit",
        "accepted_edited",
        "1300.00",
        "quarterly",
        entity_kind=None,
        entity_key=None,
        actor="a<b@example.org",
        subject=f'{{"{second}": "Bonds <i>", "{first}": ""}}',
    )
    names = {
        SHARE_LABELS: {first: "Bank check <b>", second: "Other"},
        SHARE_SHORTS: {first: "Check", second: "Other"},
    }
    (line,) = steps([edit], names, reader=reader("staff"))
    detail = line[2]
    full = (
        "Pledge; Reviewer's value: $1,300.00, Quarterly; share methods: Bank "
        "check <b>, Other: Bonds <i>; by a<b@example.org"
    )
    assert detail == full and type(str(detail)) is str
    assert (
        '<span title="Bank check &lt;b&gt;"><span aria-hidden="true">Check</span>'
        '<span class="visually-hidden">Bank check &lt;b&gt;</span></span>, '
        "Other: Bonds &lt;i&gt;; by "
    ) in detail.before
    assert detail.after == ""
    assert detail.actor.email == "a<b@example.org"
    # The page draws the actor by name between the two halves (#2259), so the
    # cell is the sentence itself, not the markup string alone.
    assert Event(line[0], line[1], detail).cell is detail
    # No short label differs: the page shows the text, escaped by its template.
    (plain,) = steps(
        [edit], {SHARE_LABELS: names[SHARE_LABELS]}, reader=reader("staff")
    )
    assert Event(plain[0], plain[1], plain[2]).cell == full


def test_limitation_lines_read_as_their_decision():
    """A Limitations review reads as its decision (#2071): an ``accepted``
    row that undoes Handled (``d``) reads Back to review with its note, a
    fresh Accept reads Accepted; the answer is named in the Limitations
    report's words, for the Member or the Family."""
    member = {"entity_kind": "member", "subject": "member"}
    family = {"entity_kind": "family", "entity_key": None, "subject": "family"}
    rows = [
        row("limitation", "member:3", 0, "accepted", d="false", **member),
        row("limitation", "member:3", 1, "handled", d="false", **member),
        row(
            "limitation", "member:3", 2, "accepted", d="true", note="Not yet", **member
        ),
        row("limitation", "family:", 3, "ignored", d="false", **family),
        row("limitation", "family:", 4, "none", d="false", note="Asked", **family),
        # A Handled the Family's new answer ended, then a fresh Accept.
        row("limitation", "member:3", 5, "handled", d="false", **member),
        row("limitation", "member:3", 6, "accepted", d="false", note="New", **member),
    ]
    assert words(rows) == [
        "Accepted",
        "Handled",
        "Back to review",
        "Ignored",
        "Back to review",
        "Handled",
        "Accepted",
    ]
    by = "; by staff@example.org"
    serve = "Cannot participate in any Ministries for Smith, Anne" + by
    attend = "Cannot attend Mass or prayer services for the Family" + by
    assert details(rows, reader("staff")) == [
        serve,
        serve,
        serve + "; note: Not yet",
        attend,
        attend + "; note: Asked",
        serve,
        serve,
    ]
    leader = reader("ministry_leader", ministries=[9])
    assert not any("note:" in detail for detail in details(rows, leader))
    # A proposed Member is named as the Limitations page names them.
    proposed = {"entity_kind": "proposed_member", "subject": "proposed_member"}
    added = row(
        "limitation", "proposed_member:p1", 0, "accepted", d="false", **proposed
    )
    assert details([added], reader("staff")) == [
        "Cannot participate in any Ministries for Smith, Anne (proposed)"
        "; by staff@example.org"
    ]


def test_request_lines_name_what_a_response_asked_for():
    """A census change or Ministry join or leave a response asked for is a
    request line (#2023): the item without who acted or the value, and no
    part of the item's review replay."""
    rows = [
        row("census_asked", "p", 0, actor=None),
        row("ministry_asked", "q", 0, c="join", subject="9", actor=None),
        row("census_review", "p", 1, "approved", "false", None, "false"),
    ]
    assert requests(rows, {9: "Choir"}) == [
        (START, ASKED["census_asked"], "Email address for Smith, Anne"),
        (START, ASKED["ministry_asked"], "Join Choir for Smith, Anne"),
    ]
    assert steps(rows, {9: "Choir"}) == [
        (
            START + MINUTE,
            "Accepted",
            "Email address for Smith, Anne; by staff@example.org",
        ),
    ]
    # The words are their own, never a review word.
    assert not {str(word) for word in ASKED.values()} & WORDS


def test_parish_added_items_read_added_by_the_parish():
    """A Ministry request the parish added reads Added by the parish for the
    revision that added it, then as any request (#2023); a census addition
    reads Added by the parish, Entered by hand, Back to review (its note for
    readers of Census changes) and Withdrawn."""
    ministry = {"c": "join", "subject": "9", "added": "true"}
    rows = [
        row("ministry_revision", "q", 1, "resolved", "joined", **ministry),
        row("ministry_revision", "q", 2, "in_progress", None, d="false", **ministry),
        row("ministry_revision", "r", 3, "resolved", "joined", c="join", subject="9"),
    ]
    assert words(rows, {9: "Choir"}) == [
        "Added by the parish",
        "Back to review",
        "Accepted",
    ]
    rows = [
        row("census_addition", "add:a", 0, note="Called"),
        row("census_addition_resolution", "add:a", 1, "entered"),
        row("census_addition_resolution", "add:a", 2, "reopened", note="Typo"),
        row("census_addition_resolution", "add:a", 3, "withdrawn", note="Gone"),
    ]
    assert words(rows) == [
        "Added by the parish",
        "Entered by hand",
        "Back to review",
        "Withdrawn",
    ]
    item = "Email address for Smith, Anne; by staff@example.org"
    assert details(rows, reader("staff")) == [item, item, item + "; note: Typo", item]
    assert details(rows, reader("ministry_leader", ministries=[9]))[2] == item
