"""The review wizard's page sections, from the Family hub's code (#2123).

``review_sections`` composes the hub's own reads and row actions for one
wizard page, a Family page (slice 2) or a Member page (slice 3): only the
run's matched items the stream's scoped read still lists are kept, the
others it lists are counted for the filtered-out line, and a subject none
of whose items is listed is not listed at all. The database reads are
replaced here by the hub unit tests' shaped rows; the PostgreSQL tests
(``test_review_wizard_sections_postgresql`` and
``test_review_wizard_members_postgresql``) read them.
"""

from dataclasses import replace
from html import unescape
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports import review_sections
from parishkit.stewardship.reports.hub_census import shape as hub_shape
from parishkit.stewardship.reports.review_sections import Part, Section, read_section

from .test_hub_actions import HOME_ID, census_worklist, hub_rows
from .test_hub_census import ANSWERS, NAMES, change

CAMPAIGN = SimpleNamespace(pk="campaign")
FAMILY = SimpleNamespace(pk="family", family_duid=1234, campaign_id="campaign")
# A second household change (the email opt-out) beside the home address.
OPT_ID = "11111111-0000-4000-8000-000000000099"


def rows():
    """The hub unit tests' change rows, plus the Family's email opt-out."""
    return [
        *hub_rows(),
        change("family", "1234", "email_opt_out", True) | {"id": OPT_ID},
    ]


def family_items():
    """The hub's Family subject's change ids, in its row order."""
    census = hub_shape(ANSWERS, NAMES, rows())
    return [row.item_id for row in census.family.changed]


def census_page(monkeypatch, stored, listed):
    """A census Family page's section: ``stored`` the run's item ids, and
    ``listed`` the ids the stream's scoped read lists for the reader."""
    by_id = {row["id"]: row for row in rows()}
    monkeypatch.setattr(
        review_sections,
        "read_hub_census",
        lambda *args, **kwargs: hub_shape(ANSWERS, NAMES, rows()),
    )
    monkeypatch.setattr(
        review_sections,
        "census_worklist",
        lambda *args, **kwargs: census_worklist(
            *({"id": item} for item in listed if item in by_id),
            prefix="review-family-census",
        ),
    )
    monkeypatch.setattr(review_sections, "mutable", lambda campaign_id: True)
    monkeypatch.setattr(review_sections, "follow", lambda model, link, ids: set(ids))
    subject = {"family": "family", "kind": "family", "key": "", "items": stored}
    return read_section("census", CAMPAIGN, FAMILY, subject, None, parish_name="")


def test_a_census_family_page_keeps_only_its_matched_changes(monkeypatch):
    """The matched change alone, with its actions and dialogs; the Family's
    other listed changes are counted; unchanged fields stay."""
    items = family_items()
    assert HOME_ID in items and len(items) > 1
    section = census_page(monkeypatch, [HOME_ID], items)
    assert section.listed
    (part,) = section.parts
    assert part.kind == "census" and part.prefix == "review-family"
    assert [row.item_id for row in part.subject.changed] == [HOME_ID]
    assert part.subject.unchanged
    assert section.filtered_out == len(items) - 1
    (dialogs,) = section.dialogs
    assert [row["id"] for row in dialogs.rows] == [HOME_ID]


def test_a_change_the_reader_may_no_longer_see_is_not_shown(monkeypatch):
    """A stored change the scoped read no longer lists is not drawn, nor
    counted; with none left the subject is not listed."""
    items = family_items()
    others = [item for item in items if item != HOME_ID]
    section = census_page(monkeypatch, [HOME_ID], others)
    assert not section.listed and section.parts == ()
    section = census_page(monkeypatch, [HOME_ID, others[0]], others)
    assert section.listed
    assert [row.item_id for row in section.parts[0].subject.changed] == [others[0]]
    assert section.filtered_out == len(others) - 1


def test_a_family_page_of_a_member_stream_reads_nothing():
    """Ministry requests has no Family page, so no Family section either."""
    subject = {"family": "family", "kind": "family", "key": "", "items": ["1"]}
    found = read_section("ministry", CAMPAIGN, FAMILY, subject, None, parish_name="")
    assert found == Section()


def render(**context):
    """The wizard's section component with ``context``."""
    return unescape(
        render_to_string("stewardship/components/review-wizard-section.html", context)
    )


def test_the_section_component_draws_parts_and_the_filtered_out_line(monkeypatch):
    """A census part through the shared census section, the plural line, and
    the stream's dialogs; a page with nothing filtered out has no line."""
    items = family_items()
    section = census_page(monkeypatch, [HOME_ID], items)
    section = Section(
        listed=True,
        parts=section.parts,
        dialogs=section.dialogs,
        filtered_out=2,
    )
    drawn = render(listed=True, member_page=False, section=section)
    assert f'<tr id="review-family-census-row-{HOME_ID}">' in drawn
    assert 'id="review-family-changed-caption"' in drawn
    assert 'id="review-family-census-decide-dialog"' in drawn
    assert (
        '<p class="help" data-review-filtered-out>2 more in this stream for '
        "this Family are filtered out.</p>"
    ) in drawn
    drawn = render(
        listed=True,
        member_page=True,
        section=Section(listed=True, parts=section.parts, filtered_out=1),
    )
    assert "1 more in this stream for this Member is filtered out." in drawn
    drawn = render(listed=True, member_page=False, section=Section(listed=True))
    assert "filtered out" not in drawn


def test_an_unlisted_subject_is_named_nowhere():
    """The section says only that the subject is no longer listed."""
    part = Part("answers", "review-answers", rows=(), actions_column=False)
    section = Section(listed=False, parts=(part,), filtered_out=3)
    drawn = render(listed=False, member_page=True, section=section)
    assert drawn.strip() == (
        "<p data-review-unlisted>This Member is no longer listed for this stream.</p>"
    )
    drawn = render(listed=False, member_page=False, section=section)
    assert "This Family is no longer listed for this stream." in drawn


def limitations_page(monkeypatch, stored, *, financial):
    """A Limitations Family page's section: ``stored`` the run's item ids;
    the Family answered Cannot attend Mass (listed for the reader) and,
    with ``financial`` (a ``HubFinancial``, or None for a reader who may
    not see pledges), Cannot contribute financially."""
    from parishkit.stewardship.reports.hub_census import FieldRow
    from parishkit.stewardship.responses.limitation_review import answer_id

    own = answer_id(FAMILY.family_duid, "family", "")
    row = FieldRow(
        "Cannot attend Mass or prayer services",
        "Yes",
        True,
        item_id=own,
        item={"id": own, "icons": []},
    )
    monkeypatch.setattr(
        review_sections,
        "_answers",
        lambda *args, **kwargs: (SimpleNamespace(actions=()), [row]),
    )
    monkeypatch.setattr(review_sections, "_pledge", lambda *args, **kwargs: financial)
    subject = {"family": "family", "kind": "family", "key": "", "items": stored}
    return own, read_section(
        "limitations", CAMPAIGN, FAMILY, subject, None, parish_name=""
    )


def cannot_give():
    """The Family's pledge, its answer Cannot contribute financially."""
    from dataclasses import replace

    from .test_hub_actions import pledge_tab

    return replace(pledge_tab(fresh=True), cannot_give=True)


def test_a_limitations_family_page_draws_both_family_answers(monkeypatch):
    """Cannot attend Mass by the answers section, Cannot contribute
    financially by the pledge section with Pledges' dialogs; every id the
    page draws is unique."""
    import re

    own, _section = limitations_page(monkeypatch, [], financial=cannot_give())
    own, section = limitations_page(
        monkeypatch, [own, "pledge-1"], financial=cannot_give()
    )
    assert section.listed and [part.kind for part in section.parts] == [
        "answers",
        "pledge",
    ]
    assert section.filtered_out == 0 and section.count == 2
    drawn = render(listed=True, member_page=False, section=section)
    assert "Cannot attend Mass or prayer services" in drawn
    assert 'id="review-answers-changed-caption"' in drawn
    assert 'id="review-pledge-caption"' in drawn
    assert 'id="hub-financial-pledges-decide-dialog"' in drawn
    ids = re.findall(r'\bid="([^"]+)"', drawn)
    assert len(ids) == len(set(ids))


def test_a_limitations_family_pages_pledge_needs_its_match_and_access(monkeypatch):
    """A financial answer the run did not match is counted, not drawn; a
    reader who may not see pledges gets neither; a page whose only item is
    the pledge draws it alone."""
    own, section = limitations_page(monkeypatch, [], financial=cannot_give())
    own, section = limitations_page(monkeypatch, [own], financial=cannot_give())
    assert [part.kind for part in section.parts] == ["answers"]
    assert section.filtered_out == 1
    own, section = limitations_page(monkeypatch, [own, "pledge-1"], financial=None)
    assert [part.kind for part in section.parts] == ["answers"]
    assert section.filtered_out == 0
    _own, section = limitations_page(monkeypatch, ["pledge-1"], financial=cannot_give())
    assert section.listed and [part.kind for part in section.parts] == ["pledge"]
    # Cannot attend Mass, listed but not matched, is the filtered-out one.
    assert section.filtered_out == 1
    _own, section = limitations_page(monkeypatch, ["pledge-1"], financial=None)
    assert not section.listed and section.parts == ()


def test_follow_takes_each_replaced_item_to_its_latest_replacement(monkeypatch):
    """Two generations of replacement are followed, one read each; an id
    that is no record (a Limitations id) is kept as it is."""
    first, second, third = (
        f"00000000-0000-4000-8000-00000000000{n}" for n in (1, 2, 3)
    )
    links = {first: second, second: third}
    reads = []

    class Rows:
        """The ``filter(...).values_list(...)`` a model's manager answers."""

        def __init__(self, pks):
            self.pks = pks

        def values_list(self, *fields):
            """Each asked-for record's (pk, replacement) while it has one."""
            return [(pk, links[pk]) for pk in self.pks if pk in links]

    def rows(pk__in, **kwargs):
        """One read of the records ``pk__in`` names."""
        reads.append(kwargs)
        return Rows([str(pk) for pk in pk__in])

    model = SimpleNamespace(objects=SimpleNamespace(filter=rows))
    found = review_sections.follow(model, "replacement", [first, "f1234"])
    assert found == {third, "f1234"}
    assert reads == [{"replacement__isnull": False}] * 3


# ------------------------------------------------------------ Member pages

STAFF_READER = Principal(UUID(int=1), frozenset({"staff"}))
LEADER_READER = Principal(UUID(int=2), frozenset({"ministry_leader"}), frozenset({9}))
# The hub unit tests' Member 5 (deceased): two changes in force.
DECEASED, DEATH_DATE = (row["id"] for row in hub_rows()[4:6])
ADDED = "6f1c1b6e-0000-4000-8000-000000000001"
ADDITION = hub_rows()[-1]["id"]


def member_page(
    monkeypatch, stream, kind, key, stored, *, principal=None, filters=None
):
    """A Member page's section, the hub's census read shaped from the hub
    unit tests' rows, every replacement lookup a no-op."""
    monkeypatch.setattr(
        review_sections,
        "read_hub_census",
        lambda *args, **kwargs: hub_shape(ANSWERS, NAMES, rows()),
    )
    monkeypatch.setattr(review_sections, "mutable", lambda campaign_id: True)
    monkeypatch.setattr(review_sections, "follow", lambda model, link, ids: set(ids))
    subject = {"family": "family", "kind": kind, "key": key, "items": stored}
    return read_section(
        stream,
        MODULES_CAMPAIGN,
        FAMILY,
        subject,
        principal or STAFF_READER,
        parish_name="",
        filters=filters,
    )


MODULES_CAMPAIGN = SimpleNamespace(
    pk="campaign",
    active_configuration=SimpleNamespace(values={"modules": ["census", "ministry"]}),
)


def listed_census(monkeypatch, *listed):
    """Census changes' scoped read lists ``listed`` (change ids) only."""
    by_id = {row["id"]: row for row in rows()}
    monkeypatch.setattr(
        review_sections,
        "census_worklist",
        lambda *args, **kwargs: census_worklist(
            *({"id": item} for item in listed if item in by_id),
            prefix=kwargs["prefix"],
        ),
    )


def test_a_census_member_page_keeps_only_its_matched_changes(monkeypatch):
    """The Member's matched change with its actions, the Member's other
    listed change counted; another Member's changes play no part."""
    listed_census(monkeypatch, HOME_ID, DECEASED, DEATH_DATE)
    section = member_page(monkeypatch, "census", "member", "5", [DECEASED])
    assert section.listed and section.census is not None
    (part,) = section.parts
    assert part.kind == "census" and part.prefix == "review-member"
    assert [row.item_id for row in part.subject.changed] == [DECEASED]
    assert section.filtered_out == 1 and section.count == 1
    (dialogs,) = section.dialogs
    assert [row["id"] for row in dialogs.rows] == [DECEASED]
    drawn = render(listed=True, member_page=True, section=section)
    assert f'<tr id="review-member-census-row-{DECEASED}">' in drawn
    assert "1 more in this stream for this Member is filtered out." in drawn


def test_a_new_member_page_draws_its_addition_and_answers(monkeypatch):
    """A New Member's page: the addition line with its stage and actions,
    then the Family's answers for the Member, as the hub's tab draws them."""
    listed_census(monkeypatch, ADDITION)
    section = member_page(monkeypatch, "census", "proposed_member", ADDED, [ADDITION])
    assert section.listed and section.count == 1 and section.filtered_out == 0
    drawn = render(listed=True, member_page=True, section=section)
    assert "Adding this Member:" in drawn
    assert f'id="review-member-census-row-{ADDITION}"' in drawn
    assert "The Family's answers for this Member" in drawn
    assert 'id="review-member-census-decide-dialog"' in drawn


def test_a_member_whose_items_left_the_read_is_not_listed(monkeypatch):
    """A Member none of whose stored changes is listed any more, and a
    Member the hub no longer has, are not listed; the census read is kept
    for the heading either way."""
    listed_census(monkeypatch, DEATH_DATE)
    section = member_page(monkeypatch, "census", "member", "5", [DECEASED])
    assert not section.listed and section.parts == ()
    assert section.census is not None
    section = member_page(monkeypatch, "census", "member", "99", [DECEASED])
    assert not section.listed


def ministry_page(
    monkeypatch,
    stored,
    *,
    visible,
    principal=None,
    filters=None,
    entity=("member", "3"),
):
    """A Ministry requests page for a Member (``entity``: Member 3 unless
    named), who asked to join Ministry 9 (``JOIN``) and leave Ministry 4
    (``LEAVE``); Ministry requests' scoped read lists ``visible`` of them.
    Returns the section, the Members choice the read was made with and the
    entity the hub's requests were read for."""
    from parishkit.stewardship.reports.hub_census import FieldRow

    requests = (
        FieldRow("Choir", "Leave", True, baseline="In this Ministry", item_id=LEAVE),
        FieldRow("Food pantry", "Join", True, item_id=JOIN),
    )
    asked = {}

    def read(campaign_id, family_id, member):
        """The Member tab's requests, noting whose were read."""
        asked["entity"] = member
        return requests

    monkeypatch.setattr(review_sections, "read_member_ministry", read)

    def worklist(campaign_id, principal, ids, *, prefix, writable, members):
        """Ministry requests' worklist of the ``visible`` requests."""
        asked.update(ids=ids, members=members, prefix=prefix)
        return census_worklist(
            *({"id": HOME_ID, "shaped": {"id": item}} for item in visible),
            prefix=prefix,
        )

    monkeypatch.setattr(review_sections, "ministry_worklist", worklist)
    section = member_page(
        monkeypatch,
        "ministry",
        *entity,
        stored,
        principal=principal,
        filters=filters,
    )
    assert asked["ids"] == {JOIN, LEAVE}
    return section, asked["members"], asked["entity"]


JOIN = "22222222-0000-4000-8000-000000000009"
LEAVE = "22222222-0000-4000-8000-000000000004"


def test_a_ministry_page_draws_only_what_the_scoped_read_lists(monkeypatch):
    """Staff see the matched request, the other counted; a leader of
    Ministry 9 only sees the join, and the leave (another Ministry's) is
    neither drawn nor counted."""
    section, members, _entity = ministry_page(
        monkeypatch, [JOIN], visible=[JOIN, LEAVE], filters={"members": "gone"}
    )
    assert members == "gone"
    (part,) = section.parts
    assert part.kind == "ministry" and [row.label for row in part.rows] == [
        "Food pantry"
    ]
    assert section.filtered_out == 1 and section.count == 1
    drawn = render(listed=True, member_page=True, section=section)
    assert 'id="review-ministry-ministry-caption"' in drawn
    assert f'<tr id="review-ministry-row-{JOIN}">' in drawn and "Choir" not in drawn
    section, members, _entity = ministry_page(
        monkeypatch,
        [JOIN, LEAVE],
        visible=[JOIN],
        principal=LEADER_READER,
        filters={"members": "gone"},
    )
    # A leader's Members choice is always the default, as the table's.
    assert members == "current"
    assert [row.item_id for row in section.parts[0].rows] == [JOIN]
    assert section.filtered_out == 0
    drawn = render(listed=True, member_page=True, section=section)
    assert "Choir" not in drawn and LEAVE not in drawn
    section, *_rest = ministry_page(
        monkeypatch, [LEAVE], visible=[JOIN], principal=LEADER_READER
    )
    assert not section.listed and section.parts == ()


def test_ministry_pages_of_a_new_or_untabbed_member(monkeypatch):
    """A New Member's requests and those of a Member the hub's census read
    gives no tab are read by the entity the run stored; a run with no
    Members choice (Basic) reads the table's default."""
    section, members, entity = ministry_page(
        monkeypatch, [JOIN], visible=[JOIN], entity=("proposed_member", ADDED)
    )
    assert entity == ("proposed_member", ADDED) and members == "current"
    assert section.listed and [row.item_id for row in section.parts[0].rows] == [JOIN]
    section, _members, entity = ministry_page(
        monkeypatch, [JOIN], visible=[JOIN], entity=("member", "99")
    )
    assert entity == ("member", "99") and section.listed


def test_a_limitations_member_page_draws_the_members_answer(monkeypatch):
    """The Member's Cannot participate answer, named as the hub's tab names
    the Member, with Limitations' actions; the Family's own answer and other
    Members' are left to their pages."""
    from parishkit.stewardship.reports import hub_streams
    from parishkit.stewardship.responses.limitation_review import answer_id

    other_row = {
        "cannot_attend": True,
        "service": {
            "members": {"3": {"cannot_serve": True}, "12": {"cannot_serve": True}}
        },
        "item": None,
    }
    monkeypatch.setattr(
        review_sections,
        "read_hub_other",
        lambda campaign_id, family_id, members, **kwargs: hub_streams.other_tab(
            other_row, members, **kwargs
        ),
    )

    def acted(campaign_id, family_duid, other, principal):
        """Each row given a Limitations row with no icons."""
        rows = tuple(
            replace(row, item={"id": row.item_id, "icons": [], "hub_row": row.item_id})
            for row in other.subject.rows
        )
        return replace(other, subject=replace(other.subject, rows=rows))

    monkeypatch.setattr(review_sections, "other_tab", acted)
    own = answer_id(FAMILY.family_duid, "member", "3")
    section = member_page(monkeypatch, "limitations", "member", "3", [own])
    assert section.listed
    (part,) = section.parts
    assert part.kind == "answers"
    assert [row.label for row in part.rows] == [
        "Cannot participate in ministries: Adams, Requested"
    ]
    assert section.count == 1
    section = member_page(monkeypatch, "limitations", "member", "3", ["other"])
    assert not section.listed and section.parts == ()
