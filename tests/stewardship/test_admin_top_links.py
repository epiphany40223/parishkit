"""No Admin page opens with links that repeat the left-hand menu (#2143).

Navigation rule 11 (UX conventions spec): the menu is the way between
pages, so a page's top (above its heading, or between its heading and its
main content) has no bare link line whose target is a menu entry. A link that
leads somewhere the menu does not (a section of a page, a filtered view, a
page outside the menu) may stay. This reads each template's source, as
``test_admin_page_names`` does, so it runs without a database.
"""

import re

from parishkit.stewardship.accounts import admin_navigation as navigation

from .test_admin_page_names import COMMENT, TEMPLATE_DIR

# Pages allowed a menu-entry link line at the top: {template: (menu entries,
# reason)}. Empty since the sweep; a new entry needs a reason the menu cannot
# serve, and the rule's spec text names it. Error pages are not listed: they
# are skipped outright, because rule 3 has an error page offer a way out and
# it has no main content for such a link to sit above.
ALLOWED = {}

ABOUT = re.compile(r"{% aboutpage .*?{% endaboutpage %}", re.S)
# Where a page's main content starts, after its heading: its first section
# heading, form, table, panel, list, include or identified region. Everything
# before it is the top. The search starts after the h1, so an include drawn
# above the heading (a wizard's step bar, a review outcome) does not end the
# top before the heading's own link lines.
MAIN = re.compile(r"<h2|<form|<table|<section|<details|<dl|<ul|<ol|<div id=|{% incl")
PARAGRAPH = re.compile(r"<p\b[^>]*>(.*?)</p>", re.S)
LINK = re.compile(r"<a\b[^>]*>.*?</a>|{% menu_link [^%]*%}", re.S)
# What may sit between a link line's links: separators and template logic.
FILLER = re.compile(r"{% (?:if|elif|else|endif)\b[^%]*%}|·|\s")
# A link to a menu entry's own address: a reversed URL that ends the href, or
# a menu_link without a query or an href of its own.
URL_TARGET = re.compile(r"href=\"{% url 'admin:(\w+)' %}\"")
# A reversed URL saved in a variable ({% url 'admin:x' as x_url %}) and used
# as a link's whole href.
URL_AS = re.compile(r"{% url 'admin:(\w+)' as (\w+) %}")
VAR_TARGET = re.compile(r'href="{{ (\w+) }}"')
MENU_TARGET = re.compile(r'{% menu_link "(\w+)"(?![^%]*\b(?:query|href)=)')


def _top(source):
    """The template's top: its content block before the main content.

    Comments and the About panel are left out: the panel is where a page's
    explanation belongs, and a comment renders nothing.
    """
    content = COMMENT.sub("", source).partition("{% block content %}")[2]
    content = ABOUT.sub("", content)
    heading = content.find("</h1>")
    match = MAIN.search(content, max(heading, 0))
    return content[: match.start()] if match else content


def _menu_links(top):
    """Menu entries linked from bare link lines (paragraphs of links only)."""
    saved = {var: name for name, var in URL_AS.findall(top)}
    names = set()
    for body in PARAGRAPH.findall(top):
        if not LINK.search(body) or FILLER.sub("", LINK.sub("", body)):
            continue
        names |= set(URL_TARGET.findall(body)) | set(MENU_TARGET.findall(body))
        names |= {saved[var] for var in VAR_TARGET.findall(body) if var in saved}
    return names & navigation.MENU_NAMES


def test_no_top_link_line_repeats_the_menu():
    """No Admin page's top has a link line to a menu entry (rule 11)."""
    found = {}
    for path in sorted(TEMPLATE_DIR.glob("*.html")):
        source = path.read_text(encoding="utf-8")
        if path.name.endswith("-error.html"):
            continue
        if "{% extends 'stewardship/admin-base.html' %}" not in source:
            continue
        if names := _menu_links(_top(source)):
            found[path.name] = names
    assert found == {name: set(entry) for name, (entry, _) in ALLOWED.items()}


def test_the_guard_sees_a_link_line():
    """The guard flags a menu link line and ignores a section link or prose.

    The include above the heading does not end the top, and a link through
    a saved URL counts as the entry's own address.
    """
    top = _top(
        "{% block content %}{% include 'steps.html' %}<h1>X</h1>"
        "{% url 'admin:background' as tasks_url %}"
        '<p><a href="{{ tasks_url }}">B</a></p>'
        "<p><a href=\"{% url 'admin:mail_queue' %}\">Q</a> · "
        '{% menu_link "deliveries" _("Sent") %}</p>'
        '<p>{% menu_link "system_health" _("Why") query="#health-mail" %}</p>'
        "<p>See <a href=\"{% url 'admin:logs' %}\">logs</a>.</p><h2>Data</h2>"
        "<p><a href=\"{% url 'admin:users' %}\">U</a></p>"
    )
    assert _menu_links(top) == {"mail_queue", "deliveries", "background"}
