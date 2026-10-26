"""The Family hub's row actions (#2064): the stream page's own icons and dialogs.

A hub tab's changes are given their items' rows as the stream's review
worklist decorates them (``hub_actions``), and the panel draws them through
the worklist's own includes, posting to the stream's page.
"""

from dataclasses import replace
from html import unescape
from uuid import UUID

from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.reports.census_changes import (
    EVERY_STAGE,
    CensusQuery,
    select,
    shape,
)
from parishkit.stewardship.reports.census_worklist import worklist_page
from parishkit.stewardship.reports.hub_actions import attach, census_subject
from parishkit.stewardship.reports.hub_census import FieldRow
from parishkit.stewardship.reports.hub_census import shape as hub_shape
from parishkit.stewardship.web.tables import paginate

from .test_census_changes import row
from .test_hub_census import ANSWERS, NAMES, ROWS, render

STAFF = frozenset({"staff"})
# The hub's Family tab's change (the home address) and the first Member's,
# with the ids their census worklist items carry.
HOME_ID = "11111111-0000-4000-8000-000000000001"
NAME_ID = "11111111-0000-4000-8000-000000000003"


def hub_rows():
    """``test_hub_census``'s change rows, each with an item id."""
    found = []
    for index, change in enumerate(ROWS, 1):
        found.append(change | {"id": f"11111111-0000-4000-8000-{index:012d}"})
    return found


def census_worklist(*items, roles=STAFF, mutable=True, prefix="hub-family-census"):
    """The census worklist a hub tab reads: ``items`` as Census changes shapes
    them, every stage, unpaged, posting to Census changes. An item's
    ``shaped`` values are set after shaping (``withdraws``: the open
    addition an Accept withdraws, as ``census_changes`` links it)."""
    query = CensusQuery(stage=EVERY_STAGE)
    rows = []
    for item in items:
        item = dict(item)
        after = item.pop("shaped", {})
        rows.append(shape(row(**item)) | after)
    result = select(rows, query, administrator=False)
    result["roles"] = roles
    result["replacements"] = {}
    worklist, _table = worklist_page(
        result,
        query,
        paginate(result["rows"], {"size": "all"}),
        "/admin/reports/census/",
        mutable=mutable,
        prefix=prefix,
    )
    worklist.answer_page = False
    return worklist


def family_tab(worklist):
    """The hub's census with the Family tab given ``worklist``'s actions."""
    census = hub_shape(ANSWERS, NAMES, hub_rows())
    return census.__class__(
        census_subject(census.family, worklist), census.members, census.census
    )


def test_a_change_is_given_its_items_row_and_stage():
    """The hub row takes the worklist row and its placement; others stay."""
    worklist = census_worklist({"id": HOME_ID, "execution": "conflict"})
    items = {item["id"]: item for item in worklist.rows}
    hub_row = FieldRow("Home address", "9 Census Way", True, item_id=HOME_ID)
    acted = attach(hub_row, items)
    assert acted.item is items[HOME_ID]
    # The stage is the worklist's (a refresh check's Conflict, here).
    assert acted.placement.stage is Stage.CONFLICT
    assert acted.stage_label == "Conflict"
    unknown = FieldRow("Nickname", "B", True, item_id="none")
    assert attach(unknown, items) is unknown
    assert attach(None, items) is None


def test_the_family_tab_draws_the_census_pages_icons_and_dialogs():
    """Icons, the decision dialog and an Edit dialog, as Census changes has."""
    page = unescape(render("family", family_tab(census_worklist({"id": HOME_ID}))))
    panel = page.split('id="family-tabs-panel-family"')[1]
    region = panel.split('id="hub-family-content"')[1]
    # The tab's content is a region redrawn from the tab's own address.
    assert 'data-in-place-region data-refresh-url="/admin/reports/families/' in panel
    assert "?tab=family" in panel.split('id="hub-family-content"')[1].split(">")[0]
    assert '<th scope="col">Actions</th>' in region
    assert f'<tr id="hub-family-census-row-{HOME_ID}"' in region
    for action in ("accept", "edit", "ignore"):
        assert f'data-row-action="{action}"' in region
    # Every icon opens a dialog drawn here, which posts to Census changes.
    assert 'data-confirm-open="hub-family-census-decide-dialog"' in region
    assert 'id="hub-family-census-decide-dialog"' in region
    assert f'id="hub-family-census-edit-dialog-{HOME_ID}"' in region
    assert 'action="/admin/reports/census/"' in region
    # The hub redraws itself: its dialogs do not take the answer as theirs.
    assert "data-confirm-answer-page" not in region
    # No bulk bar or Add on the hub: one item per row.
    assert "hub-family-census-bulk-dialog" not in region
    assert "data-select-row" not in region
    assert "also review them on the Census changes page" in region


def test_the_dialog_words_are_the_census_pages():
    """Each icon carries the same title and body the worklist gives it."""
    worklist = census_worklist({"id": HOME_ID})
    page = unescape(render("family", family_tab(worklist)))
    icons = worklist.rows[0]["icons"]
    icon = next(item for item in icons if item["action"] == "accept")
    assert f'data-confirm-body="{icon["body"]}"' in page
    assert f'data-title-one="{icon["title_one"]}"' in page


def test_no_actions_without_a_role_or_while_the_campaign_is_held():
    """A reader with no census role, or a held campaign, sees no icon."""
    for worklist in (census_worklist({"id": HOME_ID}, mutable=False), None):
        census = family_tab(worklist)
        page = unescape(render("family", census))
        panel = page.split('id="family-tabs-panel-family"')[1]
        assert '<th scope="col">Actions</th>' not in panel
        assert "data-row-action" not in panel
        assert "hub-family-census-decide-dialog" not in panel
        assert "reviewed on the Census changes page, not here." in panel


def test_an_added_members_addition_offers_its_actions_on_its_line():
    """A New Member's addition line carries its icons and is a focus row."""
    census = hub_shape(ANSWERS, NAMES, hub_rows())
    added = next(item for item in census.members if item.addition)
    assert added.addition is not None
    worklist = census_worklist(
        {
            "id": added.addition.item_id,
            "entity_kind": "proposed_member",
            "entity_key": "6f1c1b6e-0000-4000-8000-000000000001",
            "field": "new_member",
            "handling": "manual",
            "baseline_available": False,
            "baseline_value": None,
            "submitted_value": ANSWERS["proposed_members"][
                "6f1c1b6e-0000-4000-8000-000000000001"
            ],
        },
        prefix=f"hub-{added.key}-census",
    )
    members = tuple(
        census_subject(item, worklist) if item is added else item
        for item in census.members
    )
    page = unescape(render(added.key, census.__class__(census.family, members)))
    panel = page.split(f'id="family-tabs-panel-{added.key}"')[1]
    line = panel.split("Adding this Member:")[0].rsplit("<p", 1)[1]
    assert f'id="hub-{added.key}-census-row-{added.addition.item_id}"' in line
    assert "data-action-row" in line
    assert 'data-row-action="accept"' in panel
    assert f'id="hub-{added.key}-census-decide-dialog"' in panel


def test_accept_carries_the_census_pages_withdraw_warning():
    """An Accept that withdraws a parish addition says so in its dialog's
    warning, exactly as on Census changes (#2041)."""
    worklist = census_worklist(
        {"id": HOME_ID, "shaped": {"withdraws": "Home address (1 Parish Way)"}}
    )
    icons = worklist.rows[0]["icons"]
    accept = next(item for item in icons if item["action"] == "accept")
    assert accept["warning"]
    page = unescape(render("family", family_tab(worklist)))
    assert f'data-confirm-warning="{accept["warning"]}"' in page


PLEDGE_ID = UUID("22222222-0000-4000-8000-000000000002")


def pledge_tab(fresh=False, wide=False):
    """The Financial tab with Pledges' worklist for one pledge: an edited one
    To enter by hand, or with ``fresh`` one To review.

    With ``wide`` every amount is $99,999.99 (#2258): the Family's answer,
    ParishSoft's Baseline and, when edited, the Reviewer's value, so the
    browser can check each stays on one line."""
    from decimal import Decimal

    from parishkit.stewardship.reports import pledge_review_views as views
    from parishkit.stewardship.reports.hub_actions import (
        items_of,
        reviewed_rows,
        shown,
    )
    from parishkit.stewardship.reports.hub_streams import financial_tab
    from parishkit.stewardship.reports.money import MoneyAmount

    from .test_hub_streams import METADATA, shaped_pledge
    from .test_pledge_review import item, pledge_row, review

    money = {}
    found = None
    if wide:
        money = {"annual": MoneyAmount(9999999)}
        found = item(
            "to_enter_by_hand",
            review("accepted_edited", amount=Decimal("99999.99"), frequency="monthly"),
        )
    source = pledge_row(item() if fresh else found, **money)
    # One pledge in both states, so a redraw from one to the other finds
    # the same row (the browser's focus-return test).
    source = pledge_row(replace(source["item"], submission_id=PLEDGE_ID), **money)
    worklist = views.page_context(
        [source],
        views.EVERY,
        {"paging": {}, "search": ""},
        STAFF,
        mutable=True,
        notice=None,
        prefix="hub-financial-pledges",
    )["worklist"]
    worklist.answer_page = False
    if wide:
        # The longest amount inside text: "Monthly ($8,333.33 each)".
        money |= {
            "source_pledge": MoneyAmount(9999999),
            "installment": MoneyAmount(833333),
        }
    tab = financial_tab(shaped_pledge(id=source["id"], **money), METADATA)
    addition = attach(tab.pledge.addition, items_of(worklist))
    return tab.__class__(
        tab.pledge.__class__(
            tab.pledge.key,
            tab.pledge.label,
            reviewed_rows(tab.pledge.rows, addition.item),
            addition=addition,
            actions=shown(worklist, (addition,)),
        ),
        contributions=tab.contributions,
        years=tab.years,
        through=tab.through,
    )


def test_the_pledge_edit_offers_every_frequency_and_shows_the_reviewers_value():
    """The pledge's Edit is Pledges' editor with all its frequencies, and
    an edited pledge shows its Reviewer's value in the Changed table's
    column, beside the amount and the frequency it replaces, as an edited
    census field does (Administrator, R46 Part J)."""
    from parishkit.stewardship.reports.financial import FREQUENCY_LABELS
    from parishkit.stewardship.responses.financial import FREQUENCIES

    from .test_hub_streams import render as streams_render

    def panel(tab):
        """The Financial tab's panel drawn with ``tab``."""
        page = streams_render("financial", sections=("financial",), hub_financial=tab)
        return unescape(page).split('id="family-tabs-panel-financial"')[1]

    edited = panel(pledge_tab())
    assert "decide=back_to_review" in edited
    assert '<th scope="col">Reviewer\'s value</th>' in edited
    assert "Reviewer's value:" not in edited and "This pledge:" not in edited
    # The amount and frequency rows carry the Edit's values; the stage and
    # actions span the pledge's three rows, on the first, which has the id.
    annual, frequency, shares = edited.split("<tr")[2:5]
    assert annual.startswith(' id="hub-financial-pledges-row-')
    # Each amount is kept on one line (keep_money, #2258).
    assert '<td><span class="money">$1,300.00</span></td>' in annual
    assert 'rowspan="3"' in annual
    # Worded as the Family's answer beside it: with its installment.
    assert '<td>Monthly (<span class="money">$108.33</span> each)</td>' in frequency
    assert "rowspan" not in frequency
    assert "<td></td>" in shares
    assert 'action="/admin/reports/financial/pledges/"' in edited
    fresh = panel(pledge_tab(fresh=True))
    assert 'data-row-action="edit"' in fresh and "Reviewer's value" not in fresh
    assert '<th scope="col">Actions</th>' in fresh
    # Every frequency Pledges' editor lists (``pledge_review_views``).
    for key in FREQUENCIES:
        assert f">{FREQUENCY_LABELS[key]}</option>" in fresh


def test_staff_and_administrators_get_the_same_census_row_actions():
    """The role table gives an Administrator no census row action Staff lack
    (only the update run, which is no row action): both see the same icons."""
    staff = census_worklist({"id": HOME_ID})
    admin = census_worklist(
        {"id": HOME_ID}, roles=frozenset({"staff", "administrator"})
    )
    assert [icon["action"] for icon in staff.rows[0]["icons"]] == [
        icon["action"] for icon in admin.rows[0]["icons"]
    ]


def test_two_loaded_panels_share_no_element_id():
    """The Family tab and a Member tab, both acting, can share the page
    (a fetched panel): no element id of one is in the other."""
    import re

    family = family_tab(census_worklist({"id": HOME_ID}))
    member = family.members[0]
    change = member.changed[0].item_id
    acted = census_subject(
        member, census_worklist({"id": change}, prefix=f"hub-{member.key}-census")
    )
    census = family.__class__(family.family, (acted, *family.members[1:]), True)

    def ids(key):
        """The element ids in the chosen panel's markup."""
        panel = render(key, census).split(f'id="family-tabs-panel-{key}"')[1]
        panel = panel.split('class="tabs-panel"')[0]
        return set(re.findall(r'\bid="([^"]+)"', panel))

    first, second = ids("family"), ids(member.key)
    assert "hub-family-census-decide-dialog" in first
    assert f"hub-{member.key}-census-decide-dialog" in second
    assert not first & second


def test_each_acting_tab_draws_the_dialogs_focus_fallback():
    """The shared dialogs fall back to focusing ``{prefix}-notice`` when a
    decided row leaves the tab; the hub draws that element (empty and
    focusable) so focus stays in place instead of jumping to the page top."""
    worklist = census_worklist({"id": HOME_ID})
    page = unescape(render("family", family_tab(worklist)))
    assert (
        f'<p id="{worklist.notice_id}" class="visually-hidden" tabindex="-1">' in page
    )
    assert worklist.notice_id == "hub-family-census-notice"


def test_an_edits_share_methods_are_named_by_short_label():
    """The Share methods row's Reviewer's value names each of an Edit's
    methods as the Family's answer beside it does: by its short label, the
    full one its tooltip (#2198)."""
    from parishkit.stewardship.reports.hub_actions import reviewed_rows
    from parishkit.stewardship.reports.hub_streams import financial_tab

    from .test_hub_streams import METADATA, shaped_pledge

    rows = financial_tab(shaped_pledge(), METADATA).pledge.rows
    item = {
        "reviewer_parts": ("$1,300.00", "Monthly", "$108.33"),
        "reviewer_shares": [
            {"id": "a", "label": "Online giving", "short": "Online", "text": ""},
            {"id": "b", "label": "Other", "short": "Other", "text": "Bonds"},
        ],
    }
    shares = reviewed_rows(rows, item)[2].reviewer
    assert str(shares) == (
        '<span title="Online giving"><span aria-hidden="true">Online</span>'
        '<span class="visually-hidden">Online giving</span></span>; Other: Bonds'
    )
