"""Only typed values reach the canonical display functions; HTML stays escaped."""

import re
from datetime import date, time

from django import template
from django.utils.html import format_html
from django.utils.translation import gettext as _

from parishkit.stewardship.web import dates, presentation

register = template.Library()
for name in (
    "number",
    "duid",
    "phone",
    "usd",
    "percentage",
    "out_of",
    "instant",
):
    register.filter(name, getattr(presentation, name))


@register.filter
def parish_date(value, style=None):
    """A calendar date, or its ISO text from a JSON projection, in the parish format.

    Templates never format dates themselves (a guard test enforces this);
    unparseable text is shown unchanged rather than failing the whole page.
    """
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return value
    return presentation.parish_date(value, style)


@register.filter
def parish_time(value):
    """A wall-clock time, or its ISO text, with the parish style's 12/24-hour clock.

    Seconds are kept only when a schedule really uses them ("9:00:30 AM").
    Unparseable text is shown unchanged rather than failing the whole page.
    """
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        try:
            value = time.fromisoformat(value)
        except ValueError:
            return value
    text = dates.format_time(value)
    if value.second:
        minute = f":{value.minute:02d}"
        text = text.replace(minute, f"{minute}:{value.second:02d}", 1)
    return text


@register.filter
def progress_integer(value):
    """Unsupported component counts select an empty state instead of a render error."""
    if type(value) is not int or value < 0:
        return None
    try:
        presentation.number(value)
    except ValueError:
        return None
    return value


ABOUT_PAGE_KEY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


@register.tag
def aboutpage(parser, token):
    """Wrap a page's longer explanation in a collapsible "About this page" panel.

    Usage: ``{% aboutpage "setup-credential" %}…{% endaboutpage %}``. The key
    names the page type. The panel starts closed so the page's data comes
    first (#227); it is a native disclosure, so it opens with a click or the
    keyboard even without script. ui-v1.js remembers, per browser, which page
    types an Admin left open.
    """
    bits = token.split_contents()
    if len(bits) != 2:
        raise template.TemplateSyntaxError("aboutpage takes exactly one key argument.")
    nodelist = parser.parse(("endaboutpage",))
    parser.delete_first_token()
    return AboutPageNode(parser.compile_filter(bits[1]), nodelist)


class AboutPageNode(template.Node):
    """Render the panel; the key must be a short slug so it is safe in markup."""

    def __init__(self, key, nodelist):
        self.key = key
        self.nodelist = nodelist

    def render(self, context):
        key = str(self.key.resolve(context))
        if not ABOUT_PAGE_KEY.fullmatch(key):
            raise template.TemplateSyntaxError(f"Invalid aboutpage key: {key!r}")
        return format_html(
            '<details class="about-page" data-about-page="{}">'
            '<summary>{}</summary><div class="about-page-body">{}</div></details>',
            key,
            _("About this page"),
            # Template output is already escaped and marked safe.
            self.nodelist.render(context),
        )


@register.inclusion_tag("stewardship/table-sort-heading.html", takes_context=True)
def sort_heading(context, table, column, label, css_class=""):
    """A column heading that re-sorts a shared Admin table (web/tables.py).

    Usage: ``{% sort_heading table "created" _("Created") "numeric" %}``.
    ``column`` must be one of the table's sortable columns (a typo fails
    loudly instead of silently rendering a dead heading). A GET table's
    heading is a link; a POST table's is a small CSRF form whose button
    carries the private filters as hidden fields. The sorted column carries
    ``aria-sort``, and each control names the direction it will choose.
    """
    return {
        "table": table,
        "column": column,
        "label": label,
        "css_class": css_class,
        "state": table.aria_sort(column),
        "descends": table.sort_descends(column),
        "fields": table.heading_fields(column),
        "query": table.heading_query(column),
        "csrf_token": context.get("csrf_token"),
    }
