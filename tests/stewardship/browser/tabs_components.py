"""A page with the shared tab component, from synthetic tabs (#1712).

The Family hub (#1678) is the first page planned to draw tabs; this fixture
isolates the component (``components/tabs.html``, ``web/tabs.py`` and the
tab behaviour in ``ui-v1.js``) in the real Admin chrome. Its tabs follow the
hub's design: a header strip above them, then Timeline, Family, one tab per
Member, Financial (with a count) and Cannot participate and Additional
information, plus one unavailable tab with its reason. Filler above the
tabs puts the row below the fold, so a test can tell an in-place switch from
a jump to the top. The page ends with the panels and the Timeline panel is
much taller than the others, so switching to a shorter panel would pull the
page (and the tab row) down if nothing held its height.

The page is served at ``PAGE`` and at ``PAGE?tab=<key>`` for each available
tab, each drawn with only that tab's panel, as a real view would draw it.
A Save form (an in-place POST, which a test holds open with a route) shows
that a tab is not switched while a save is in flight.
"""

from django.template import engines

from parishkit.stewardship.web.tabs import Tab, tab_set

PAGE = "/tabs"
PAGE_TEMPLATE = """{% extends 'stewardship/admin-base.html' %}
{% block title %}Tabs{% endblock %}
{% block content %}
<h1>Family information</h1>
<section id="saved" data-in-place-region><form data-in-place method="post"
 action="/tabs/save#saved" data-in-place-message="Saved.">
<button id="save" type="submit">Save</button></form></section>
{% for n in filler %}<p>Filler paragraph {{ n }} above the tabs.</p>{% endfor %}
<p id="header-strip">Example, Ann and Bob · DUID 1234</p>
{% include 'stewardship/components/tabs.html' with tabs_id='family-tabs' \
label='Family information' tabs=tabs %}
{% endblock %}"""
# Each panel names its tab and has a line count of its own: the Timeline is
# far taller than the rest. A link in each panel is the first thing Tab
# reaches after the panel itself.
PANEL_TEMPLATE = """<h2 id="{{ tab.key }}-heading">{{ tab.label }} panel</h2>
<p data-panel-text>Drawn for {{ tab.key }}.</p>
<p><a href="#{{ tab.key }}-heading">Link in {{ tab.key }}</a></p>
{% for n in lines %}<p>{{ tab.label }} line {{ n }}</p>{% endfor %}"""
UNAVAILABLE = "Census is not part of this campaign."
# (key, label, count, lines in the panel)
TABS = (
    ("timeline", "Timeline", None, 60),
    ("family", "Family", None, 3),
    ("member-1", "Ann Example", None, 3),
    ("member-2", "Bob Example", None, 3),
    ("financial", "Financial", 3, 3),
    ("other", "Cannot participate and Additional information", None, 3),
)
KEYS = tuple(key for key, *_ in TABS)
FILLER = range(30)


def tabs(parameters):
    """The fixture's ``TabSet`` for an address's parameters."""
    panel = engines["django"].from_string(PANEL_TEMPLATE)
    drawn = [
        Tab(key, label, template=panel, count=count) for key, label, count, _ in TABS
    ]
    drawn.insert(2, Tab("census", "Census", unavailable=UNAVAILABLE))
    return tab_set(PAGE, parameters, drawn, carry=(("family", "1234"),))


def components(context, admin):
    """The page with no tab chosen and with each available tab chosen.

    Every tab's link carries ``family=1234`` (the page's own parameter), so
    the served addresses are ``PAGE?family=1234&tab=<key>``; the bare
    ``PAGE`` shows the first tab.
    """
    page = engines["django"].from_string(PAGE_TEMPLATE)
    lines = dict((key, range(count)) for key, _, _, count in TABS)

    def render(parameters):
        """The page for ``parameters``, its chosen panel's lines in context."""
        chosen = tabs(parameters)
        extra = {"tabs": chosen, "filler": FILLER, "lines": lines[chosen.selected]}
        return ("text/html", page.render(context | {"admin_chrome": admin} | extra))

    pages = {PAGE: render({})}
    for key in KEYS:
        pages[f"{PAGE}?family=1234&tab={key}"] = render({"tab": key})
    return pages
