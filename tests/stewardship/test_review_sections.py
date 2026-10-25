"""The review wizard's page sections, from the Family hub's code (#2123 slice 2).

``review_sections`` composes the hub's own reads and row actions for one
wizard page: only the run's matched items the stream's scoped read still
lists are kept, the others it lists are counted for the filtered-out line,
and a subject none of whose items is listed is not listed at all. The
database reads are replaced here by the hub unit tests' shaped rows; the
PostgreSQL tests (``test_review_wizard_sections_postgresql``) read them.
"""

from html import unescape
from types import SimpleNamespace

from django.template.loader import render_to_string

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
