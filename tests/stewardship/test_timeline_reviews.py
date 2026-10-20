"""The Timeline's review lines, worded from plain history rows (#1774).

``timeline_reviews.steps`` replays each item's stored history and words
each step from the state before and after it; these tests drive it with
the rows ``_HISTORY`` returns, so no database is needed. The PostgreSQL
test (``database/test_timeline_reviews_postgresql.py``) checks the
statement against rows the real review services store.
"""

from datetime import timedelta

from parishkit.stewardship.reports import timeline_pdf
from parishkit.stewardship.reports.timeline_reviews import WORDS, steps

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
    """Revisions and ticks of one request replay together, in time order."""
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
    ]
    assert words(rows, {9: "Choir"}) == [
        "Follow-up updated",
        "Accepted",
        "Entered by hand",
        "Entered by hand cleared",
        "Back to review",
        "Ignored",
        "Ignored",
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
    """Needed is Accepted (To handle), followed up is Handled, neither is To review."""
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
        "Handled cleared",
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
