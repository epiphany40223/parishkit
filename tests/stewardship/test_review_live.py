"""Review regions that update themselves (#2174; web/review_live.py).

Pure tests of the shared contract: how a region's read is parsed, that an
item's token follows what its row shows and nothing drawn anew each time,
how a stream page narrows its rows to the changed ones, and the markup the
shared templates draw for it. The pages' own reads are in
database/test_review_live_postgresql.py; the redraw in the browser is in
browser/test_review_live.py.
"""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.http import QueryDict
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy

from parishkit.stewardship.accounts.review_work import Card
from parishkit.stewardship.pipeline import Decision, Placement, Stage
from parishkit.stewardship.web import review_live
from parishkit.stewardship.web.tables import paginate

from .test_review_worklist import made, rows

TEMPLATES = Path(__file__).parents[2] / "src/parishkit/stewardship/accounts/templates"


def request(query, method="GET"):
    """A request carrying ``query`` as its address's query string."""
    return SimpleNamespace(method=method, GET=QueryDict(query))


def pair(row):
    """A drawn row as its region sends it."""
    return f"{review_live.item_key(row['id'])}.{review_live.item_token(row)}"


def decorated(shown=None):
    """The shared rows, decorated as a stream page draws them."""
    return made(shown=shown).rows


def test_a_read_names_its_items_and_nothing_else_is_one():
    """``drawn`` is None for any other request; a read is its pairs."""
    assert review_live.drawn(request("")) is None
    assert review_live.drawn(request("stage=all")) is None
    assert review_live.drawn(request("live=")) == {}
    assert review_live.drawn(request("live=abcdefgh.ABC-_9,ijklmnop.zzzzzz")) == {
        "abcdefgh": "ABC-_9",
        "ijklmnop": "zzzzzz",
    }


@pytest.mark.parametrize(
    "query,method",
    [
        # Only a GET is a read: a POST is the reader's own action.
        ("live=abcdefgh.ABC-_9", "POST"),
        # One value, never repeated.
        ("live=abcdefgh.ABC-_9&live=ijklmnop.zzzzzz", "GET"),
        # A key and a token of their own lengths and alphabet.
        ("live=abcdefg.ABC-_9", "GET"),
        ("live=abcdefgh.ABC-_", "GET"),
        ("live=abcdefgh", "GET"),
        ("live=abcdefg%21.ABC-_9", "GET"),
    ],
)
def test_a_malformed_read_is_refused(query, method):
    """Anything else is a ValueError, the page's 400."""
    with pytest.raises(ValueError):
        review_live.drawn(request(query, method))


def test_a_read_names_at_most_its_limit():
    """A region sends at most MAX_LIVE items per read (ui-v1.js batches)."""
    many = ",".join(f"{index:08d}.aaaaaa" for index in range(review_live.MAX_LIVE))
    assert len(review_live.drawn(request(f"live={many}"))) == review_live.MAX_LIVE
    with pytest.raises(ValueError):
        review_live.drawn(request(f"live={many},99999999.aaaaaa"))
    # Its request line stays well inside the web server's 8190 bytes.
    assert len(f"GET /admin/reports/census/?live={many} HTTP/1.1") < 7000


def test_a_key_is_short_stable_and_scoped():
    """The same id within the same scope is always the same key."""
    key = review_live.item_key("3f2c")
    assert key == review_live.item_key("3f2c") and len(key) == review_live.KEY_CHARS
    assert review_live.item_key("3f2c", "census") != key


def test_a_token_follows_what_the_row_shows_not_its_fresh_keys():
    """Two draws of the same rows give the same tokens, though every icon's
    request key is new; a new stage, decision or note changes the token."""
    first, second = decorated(), decorated()
    assert first[0]["icons"][0]["post"] != second[0]["icons"][0]["post"]
    assert [review_live.item_token(row) for row in first] == [
        review_live.item_token(row) for row in second
    ]
    moved = rows()
    moved[0]["placement"] = Placement(Stage.READY_TO_UPDATE, Decision.ACCEPTED)
    assert review_live.item_token(decorated(moved)[0]) != review_live.item_token(
        first[0]
    )
    noted = rows()
    noted[1]["note"] = "Checked with the Family"
    assert review_live.item_token(decorated(noted)[1]) != review_live.item_token(
        first[1]
    )


def test_a_token_reads_lazy_text_and_dataclasses_by_value():
    """A card's label is lazy text: its token is its value's, every time."""
    card = Card(gettext_lazy("To review"), "/admin/reports/census/", count=3)
    again = Card(gettext_lazy("To review"), "/admin/reports/census/", count=3)
    assert review_live.item_token(card) == review_live.item_token(again)
    assert review_live.item_token(card) != review_live.item_token(
        replace(card, count=4)
    )


def test_pick_keeps_the_drawn_items_and_changed_only_the_changed_ones():
    """A read's rows are the drawn ones; its answer the ones that changed."""
    shown = decorated()
    live = review_live.drawn(request("live=" + ",".join(pair(row) for row in shown)))
    assert review_live.pick(rows(), None) == rows()
    assert [row["id"] for row in review_live.pick(rows(), live)] == ["0", "1", "2"]
    only = {review_live.item_key("1"): "aaaaaa"}
    assert [row["id"] for row in review_live.pick(rows(), only)] == ["1"]
    assert review_live.changed(shown, live) == []
    moved = rows()
    moved[2]["placement"] = Placement(Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED)
    assert [row["id"] for row in review_live.changed(decorated(moved), live)] == ["2"]
    assert review_live.changed(shown, None) == shown


def test_narrow_keeps_the_worklist_and_its_table_alike():
    """A stream page's worklist and table both keep only the changed rows."""
    table = paginate(rows(), {"size": "25"})
    worklist = made(shown=table.rows)
    table = replace(table, rows=worklist.rows)
    live = {review_live.item_key("1"): "aaaaaa"}
    narrowed, page, count = review_live.narrow(worklist, table, live)
    assert count == 1
    assert [row["id"] for row in narrowed.rows] == ["1"]
    assert [row["id"] for row in page.rows] == ["1"]


def render_worklist(shown=None, **options):
    """The shared component drawn for the shared rows."""
    table = paginate(rows() if shown is None else shown, {"size": "25"})
    worklist = made(shown=table.rows, **options)
    table = replace(table, rows=worklist.rows)
    html = render_to_string(
        "stewardship/components/review-worklist.html",
        {"worklist": worklist, "table": table, "csrf_token": "x"},
    )
    return worklist, html


def test_the_worklist_draws_its_region_items_tags_and_parts():
    """The table region names its address and view form; each row its key
    and token, with its tag hidden beside the stage; each row form its part."""
    worklist, html = render_worklist()
    assert (
        'data-table-region data-live-items="/review" data-live-view="census-decide"'
    ) in html
    for row in worklist.rows:
        assert (
            f'<tr id="census-row-{row["id"]}" data-live-item='
            f'"{review_live.item_key(row["id"])}" data-live-token='
            f'"{review_live.item_token(row)}" data-live-seen='
            f'"{review_live.seen_token(row)}">'
        ) in html
    # Only the changed rows answer a check, so the table is never redrawn
    # whole from one (ui-v1.js redraws only a [data-live-whole] region).
    assert "data-live-whole" not in html
    assert html.count('class="live-changed" data-live-tag hidden') == 3
    assert "Changed by someone else" in html
    edit = review_live.item_key("0")
    assert 'id="census-edit-0" method="post" action="/review"' in html
    assert f'data-require-complete data-live-part="{edit}"' in html
    # A stream that reads its own address with more (Ministry requests'
    # remembered view) names it instead.
    _, html = render_worklist(live_address="/review?queue=abc")
    assert 'data-live-items="/review?queue=abc"' in html


def test_every_drawn_item_is_counted_by_changed_count():
    """A rendered page's items, compared with the drawn ones, as the Family
    hub's tab check does."""
    worklist, html = render_worklist()
    drawn = {
        review_live.item_key(row["id"]): review_live.item_token(row)
        for row in worklist.rows
    }
    assert review_live.changed_count(html, drawn) == 0
    stale = drawn | {review_live.item_key("2"): "aaaaaa"}
    assert review_live.changed_count(html, stale) == 1
    # An item the answer no longer draws is not a change.
    assert review_live.changed_count(html, {"zzzzzzzz": "aaaaaa"}) == 0


def test_review_work_cards_are_items_only_on_review_work():
    """A Review work card carries its key within its panel's stream; the
    other link cards draw none."""
    card = Card("To review", "/admin/reports/census/", count=2)
    html = render_to_string(
        "stewardship/review-work-panels.html",
        {
            "panels": [SimpleNamespace(stream="census", title="Census", cards=[card])],
            "nested": True,
        },
    )
    assert (
        f'data-live-item="{review_live.item_key("To review", "census")}" '
        f'data-live-token="{review_live.item_token(card)}"'
    ) in html
    plain = render_to_string(
        "stewardship/components/link-cards.html",
        {"cards": [card], "cards_labelledby": "x"},
    )
    assert "data-live-item" not in plain


def test_a_read_may_name_its_tables_columns():
    """``live_columns`` names the optional columns the table draws; anything
    else, or a repeated value, is refused; ``alone`` admits only the two."""
    found = review_live.drawn(request("live=&live_columns=now-select"))
    assert found.columns == frozenset({"now", "select"})
    assert review_live.drawn(request("live=&live_columns=")).columns == frozenset()
    assert review_live.drawn(request("live=")).columns is None
    for query in ("live=&live_columns=wide", "live=&live_columns=now&live_columns=now"):
        with pytest.raises(ValueError):
            review_live.drawn(request(query))
    assert review_live.alone(request("live=&live_columns=now"))
    assert not review_live.alone(request("live=&stage=all"))
    assert not review_live.alone(request("stage=all"))


def test_narrow_draws_the_pages_columns_not_the_changed_rows():
    """A changed row is drawn with the page's optional columns: ParishSoft
    now and the selection boxes as the page's table has them."""
    table = paginate(rows(), {"size": "25"})
    worklist = made(shown=table.rows)
    table = replace(table, rows=worklist.rows)
    assert worklist.live_columns == "select"
    live = review_live.drawn(
        request(f"live={review_live.item_key('1')}.aaaaaa&live_columns=now")
    )
    narrowed, _page, _count = review_live.narrow(worklist, table, live)
    assert narrowed.shows_now and not narrowed.selectable
    assert narrowed.live_columns == "now"
    _, html = render_worklist()
    assert 'data-live-columns="select"' in html


def test_narrow_keeps_the_selection_column_when_bulk_is_no_longer_offered():
    """A campaign held since the page was drawn offers no bulk action (the
    view passes no roles): a changed row keeps its table's selection cell,
    empty, so it fits its table and never replaces it."""
    table = paginate(rows(), {"size": "25"})
    worklist = made(roles=frozenset(), shown=table.rows)
    table = replace(table, rows=worklist.rows)
    assert not worklist.bulk and not worklist.selectable
    live = review_live.drawn(
        request(f"live={review_live.item_key('1')}.aaaaaa&live_columns=select")
    )
    narrowed, page, _count = review_live.narrow(worklist, table, live)
    assert narrowed.selectable and narrowed.live_columns == "select"
    html = render_to_string(
        "stewardship/components/review-worklist.html",
        {"worklist": narrowed, "table": page, "csrf_token": "x"},
    )
    assert '<td class="select-col"></td>' in html
    # And the other way: a page drawn without the column keeps none.
    held = review_live.drawn(
        request(f"live={review_live.item_key('1')}.aaaaaa&live_columns=")
    )
    assert not review_live.narrow(made(shown=table.rows), table, held)[0].selectable


def test_the_seen_token_follows_what_the_reader_sees_not_what_is_offered():
    """Offered actions alone (a held campaign's rows offer none) change the
    token but not the seen token, so the redrawn row shows no tag; a name,
    Submitted time or stage note changes both."""
    first = decorated()[0]
    held = made(roles=frozenset()).rows[0]
    assert review_live.item_token(held) != review_live.item_token(first)
    assert review_live.seen_token(held) == review_live.seen_token(first)
    for name, value in (
        ("who", "Member 9"),
        ("submitted_at", datetime(2054, 10, 6, 12, tzinfo=UTC)),
        ("stage_note", "ParishSoft changed since."),
    ):
        changed = rows()
        changed[0][name] = value
        row = decorated(changed)[0]
        assert review_live.seen_token(row) != review_live.seen_token(first), name
        assert review_live.item_token(row) != review_live.item_token(first), name


def test_an_identity_cells_link_is_not_compared():
    """An item page's link carries the remembered view (``queue``), which a
    check may hold differently while the cell shows the same name."""
    first = decorated()[0]
    relinked = dict(first)
    relinked["identity"] = [
        (value, column, "/item/0/?queue=other", *rest)
        for value, column, _, *rest in first["identity"]
    ]
    assert review_live.item_token(relinked) == review_live.item_token(first)


def test_a_check_is_any_request_naming_live():
    """``checking`` is how every stream view reads a check passively,
    whatever its headers say."""
    assert review_live.checking(request("live="))
    assert review_live.checking(request("live=abcdefgh.ABC-_9&stage=all"))
    assert not review_live.checking(request("stage=all"))


@pytest.mark.parametrize(
    "template",
    [
        "stewardship/hub-census-panel.html",
        "stewardship/hub-financial-panel.html",
        "stewardship/hub-other-panel.html",
    ],
)
def test_only_the_hub_tabs_are_whole_regions(template):
    """A hub tab's answer is its own whole page: its region alone is marked
    ``data-live-whole``, which ui-v1.js may redraw whole for a misfit."""
    source = (TEMPLATES / template).read_text()
    assert 'data-live-items="{{ hub_tab_url }}" data-live-whole' in source
