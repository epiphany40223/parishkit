"""Only typed values reach the canonical display functions; HTML stays escaped."""

import re
from datetime import date, time

from django import template
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from parishkit.stewardship.campaigns import single_campaign
from parishkit.stewardship.web import dates, presentation
from parishkit.stewardship.web.buttons import portal_classes

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


@register.inclusion_tag("stewardship/components/disabled-control.html")
def multi_campaign_control(control_id, label):
    """A greyed-out multi-campaign control with the one #145 tip (rule 10).

    Every control whose only purpose is working with more than one campaign
    shows the same tip, defined once in ``campaigns.single_campaign``.
    """
    return {"control_id": control_id, "label": label, "tip": single_campaign.TIP}


# A {% button %} argument: a name (HTML attribute names keep their hyphens)
# with an optional =value.
BUTTON_ARGUMENT = re.compile(r"([a-z][a-z0-9-]*)(?:=(.+))?\Z", re.S)
# Present or absent by truthiness, never rendered with a value.
BOOLEAN_ATTRIBUTES = {"disabled", "hidden", "formnovalidate", "autofocus"}


@register.tag
def button(parser, token):
    """The shared portal button (web/buttons.py; guarded by test_button_guard).

    Usage::

        {% button type="submit" variant="secondary" name="action" value=row.id
           enabled=mutable data-bulk-action %}{% translate "Save" %}{% endbutton %}

    Arguments render as attributes in the order written, so a migrated
    button's markup is unchanged. ``variant`` (primary, secondary, large or
    link; primary by default) and ``class`` (extra classes) render one class
    attribute where the first of them appears. ``enabled=x`` renders
    ``disabled`` when ``x`` is false; ``disabled``, ``hidden`` and the other
    boolean attributes render by truthiness. A bare name (``data-bulk-action``)
    or a value of True renders as a bare attribute, except that an ``aria-*``
    attribute always spells its state: True and False render as "true" and
    "false" (ARIA reads a bare aria-pressed as an empty, invalid token). Any
    other value is escaped, and omitted only when it is None or False.
    ``href`` makes the button a link styled as one, which takes no ``type``
    and cannot be disabled (a link has no disabled state); a <button> must
    name its ``type``.
    """
    arguments = []
    for bit in token.split_contents()[1:]:
        match = BUTTON_ARGUMENT.match(bit)
        if match is None:
            raise template.TemplateSyntaxError(f"button: bad argument {bit!r}")
        name, value = match.groups()
        if any(name == seen for seen, _value in arguments):
            raise template.TemplateSyntaxError(f"button: {name} given twice")
        compiled = None if value is None else parser.compile_filter(value)
        arguments.append((name, compiled))
    names = {name for name, _value in arguments}
    if ("href" in names) == ("type" in names):
        raise template.TemplateSyntaxError(
            "button: give a <button> its type, or a link its href (not both)."
        )
    if "href" in names and names & {"enabled", "disabled"}:
        raise template.TemplateSyntaxError(
            "button: a link cannot be disabled; leave it out instead."
        )
    nodelist = parser.parse(("endbutton",))
    parser.delete_first_token()
    return ButtonNode(arguments, nodelist)


class ButtonNode(template.Node):
    """Render one {% button %}: attributes in written order, then the label."""

    def __init__(self, arguments, nodelist):
        self.arguments = arguments
        self.nodelist = nodelist
        self.link = any(name == "href" for name, _value in arguments)

    def render(self, context):

        values = {
            name: True if value is None else value.resolve(context)
            for name, value in self.arguments
        }
        # {% url … as x %} renders "" for an unknown route instead of failing,
        # so an empty link address fails here rather than shipping a dead link.
        if self.link and not values["href"]:
            raise ValueError("button: a link button needs an address")
        classes = portal_classes(
            values.get("variant", "primary"), values.get("class"), link=self.link
        )
        # A link always has a class; put it first when no argument places it.
        placed = not classes or any(name in values for name in ("variant", "class"))
        parts = [] if placed else [f' class="{classes}"']
        for name, value in values.items():
            if name in ("variant", "class"):
                if classes:
                    parts.append(f' class="{conditional_escape(classes)}"')
                    classes = ""
            elif name == "enabled":
                parts.append("" if value else " disabled")
            elif name in BOOLEAN_ATTRIBUTES:
                parts.append(f" {name}" if value else "")
            elif name.startswith("aria-") and isinstance(value, bool):
                parts.append(f' {name}="{str(value).lower()}"')
            elif value is True:
                parts.append(f" {name}")
            elif value is not None and value is not False:
                parts.append(f' {name}="{conditional_escape(value)}"')
        tag = "a" if self.link else "button"
        label = self.nodelist.render(context)
        return mark_safe(f"<{tag}{''.join(parts)}>{label}</{tag}>")
