"""Only typed values reach the canonical display functions; HTML stays escaped."""

import re
from datetime import date, time

from django import template
from django.urls import reverse
from django.utils.html import conditional_escape, format_html, format_html_join
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from parishkit.stewardship.accounts import admin_navigation
from parishkit.stewardship.accounts.sessions import freshness
from parishkit.stewardship.campaigns import single_campaign
from parishkit.stewardship.campaigns.configuration import REMINDER_WORKGROUP_NAME
from parishkit.stewardship.source.source_status import source_status_label
from parishkit.stewardship.web import dates, presentation
from parishkit.stewardship.web.buttons import portal_classes
from parishkit.stewardship.web.exports import DEFAULT_EXPORT_FORMAT

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


@register.simple_tag
def reminder_workgroup_name():
    """The displayed name of the campaign's reminder exclusion setting (#1061).

    Templates take it from its one source, so renaming it is one line.
    """
    return _(REMINDER_WORKGROUP_NAME)


@register.simple_tag(takes_context=True)
def fresh_sign_in(context):
    """Whether a step-up gated action would be admitted now (#1159).

    ``{% fresh_sign_in as fresh %}`` lets a page show its "needs a sign-in
    from the last five minutes" explanation only when the reader would be
    asked to confirm, without each view computing it. It reuses the views'
    own ``freshness`` check, so every page answers the same way. A context
    without a request or a signed-in session is not fresh, so the hint shows.
    It is presentation only: each gated command still calls ``require_fresh``,
    and a sign-in that ages after the page is drawn gets that step-up answer,
    which explains itself.
    """
    request = getattr(context, "request", None)
    return request is not None and freshness(request)[0]


@register.simple_tag
def export_format_options(*formats, disabled=""):
    """The ``<option>`` list of an export form's format menu (#1190).

    ``formats`` are the form's offered values. The default
    (``DEFAULT_EXPORT_FORMAT``, XLSX) comes first and is selected, and the
    rest keep the given order, so no form picks its own default and a new
    export cannot forget the rule. The PDF-first comparison and Ministry
    packet forms are documents, not data, and keep their own menus.

    ``disabled`` names one offered format that is shown but unavailable
    (for example a PDF above its row limit); an empty value disables none.
    A disabled default is not selected, so the browser falls back to the
    first available option.
    """
    labels = {"jsonl": _("JSON Lines")}
    ordered = sorted(formats, key=lambda name: name != DEFAULT_EXPORT_FORMAT)
    return format_html_join(
        "",
        '<option value="{}"{}{}>{}</option>',
        (
            (
                name,
                " selected"
                if name == DEFAULT_EXPORT_FORMAT and name != disabled
                else "",
                " disabled" if name == disabled else "",
                labels.get(name, name.upper()),
            )
            for name in ordered
        ),
    )


@register.simple_tag
def source_status(status):
    """The shared mark for a Member or Family no longer in ParishSoft (#1216).

    Usage: ``{% source_status row.member_status %}`` or
    ``{% source_status 'missing' %}``. Renders a ``status-badge`` whose text
    is the whole phrase from ``source/source_status.py``, so screen readers
    read the same words and no tooltip is needed; renders nothing for a
    current one (``None`` or ``current``).
    """
    label = source_status_label(status)
    if not label:
        return ""
    return format_html(
        '<span class="status-badge" data-source-status="{}">{}</span>',
        status,
        _(label),
    )


def _calendar(value):
    """A calendar date from a date or its ISO text; None for blanks, and
    unparseable text unchanged so a page shows it rather than failing."""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value
    return value


@register.filter
def parish_date(value, style=None):
    """A calendar date, or its ISO text from a JSON projection, in the parish format.

    Templates never format dates themselves (a guard test enforces this);
    unparseable text is shown unchanged rather than failing the whole page.
    """
    value = _calendar(value)
    if value is None or isinstance(value, str):
        return value or ""
    return presentation.parish_date(value, style)


@register.filter
def parish_date_compact(value):
    """:func:`parish_date` in the compact form every Admin table cell uses (#1093).

    "Jan 5, 2027" or "05/01/27" rather than "January 5, 2027", so table rows
    stay short; headings and sentences keep :func:`parish_date`.
    """
    value = _calendar(value)
    if value is None or isinstance(value, str):
        return value or ""
    return dates.format_date(value, compact=True)


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
    keyboard with no script of its own. ui-v1.js remembers, per browser,
    which page types an Admin left open.
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


# Query fields that order, page or place the results rather than filter them:
# a report's sort, its page number and the browser's time zone.
NOT_FILTERS = frozenset({"sort", "page", "zone"})


def applied_filters(query, ignore=()):
    """Count a report query's fields that differ from its dataclass defaults.

    A report's query is a frozen dataclass whose defaults are the page's
    unfiltered view (an Additional information default such as "Current
    actionable requests" is the page's normal list, so it does not count).
    Sort, page and zone are left out (``NOT_FILTERS``), as is any field
    named in ``ignore``: one another choice currently overrides.
    """
    return sum(
        getattr(query, name) != field.default
        for name, field in query.__dataclass_fields__.items()
        if name not in NOT_FILTERS and name not in ignore
    )


@register.tag
def filterpanel(parser, token):
    """Wrap a report's filter form in a collapsible "Filters" panel (#227).

    Usage: ``{% filterpanel query %}<form id="table-filters" …>…</form>
    {% endfilterpanel %}``, or ``{% filterpanel query extra %}`` where
    ``extra`` counts a choice kept outside the query (the Family directory's
    Include mailing columns), or ``{% filterpanel query extra "field" %}``
    where, while that choice is on, the named query field is not counted
    because the choice overrides it (mailing columns always use "By postal
    mail only", so the reader's own Campaign mail can reach is not applied,
    #951). The panel starts closed so the report's data
    comes first, and renders open whenever any filter is applied, so an
    applied filter is never hidden behind it. Its summary names how many are
    applied; that count is a data-table-sync node with a stable id, so it
    follows an in-place Apply while the panel and the form keep their
    elements (ui-v1.js ``syncControls``).
    """
    bits = token.split_contents()
    if len(bits) not in (2, 3, 4):
        raise template.TemplateSyntaxError(
            "filterpanel takes a query, an optional extra choice and the "
            "query field that choice overrides."
        )
    nodelist = parser.parse(("endfilterpanel",))
    parser.delete_first_token()
    return FilterPanelNode([parser.compile_filter(bit) for bit in bits[1:]], nodelist)


class FilterPanelNode(template.Node):
    """Render the panel, open with its count when any filter is applied."""

    def __init__(self, arguments, nodelist):
        self.arguments = arguments
        self.nodelist = nodelist

    def render(self, context):
        query, extra, overridden = (
            [argument.resolve(context) for argument in self.arguments] + [False, None]
        )[:3]
        ignore = (overridden,) if extra and overridden else ()
        count = applied_filters(query, ignore) + int(bool(extra))
        if count:
            label = ngettext("%(count)s applied", "%(count)s applied", count) % {
                "count": presentation.number(count)
            }
        else:
            label = _("none applied")
        return format_html(
            '<details class="filter-panel" data-filter-panel{}>'
            '<summary>{}: <span id="table-filters-applied" data-table-sync>{}</span>'
            "</summary>{}</details>",
            mark_safe(" open") if count else "",
            _("Filters"),
            label,
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


# The destructive row actions table_actions draws, each with the icon it
# shows: each has its own words ("Delete Reminder 2", "Revoke nightly
# export", "Sign out jane@example.org’s session"), the same size and the same
# confirmation dialog. Sign out ends a session, as Revoke does, so it shares
# Revoke's icon (Signed-in sessions, #953).
TABLE_ACTION_VERBS = {
    "delete": "delete",
    "revoke": "revoke",
    "remove": "remove",
    "sign_out": "revoke",
}


@register.inclusion_tag("stewardship/components/table-actions.html")
def table_actions(
    name,
    edit=None,
    delete=None,
    confirm=None,
    field="id",
    verb="delete",
    confirm_name=None,
):
    """One row's Actions cell in an action table (#879).

    Usage: ``{% table_actions name=row.label edit=row.edit_url
    delete=row.id confirm="schedule-delete" field="schedule_id" %}``.
    ``name`` names the row in each button's accessible name and tooltip;
    ``edit`` is the row's edit page; ``delete`` the value its destructive
    action posts as ``field`` once the dialog ``confirm`` names is
    confirmed. ``verb`` names that action: "delete" (the default),
    "revoke" (Automation access), "remove" (Past campaigns, whose Remove
    keeps a tombstone) or "sign_out" (Signed-in sessions).
    ``confirm_name`` is what the dialog's title and announcement call the
    row, when that needs more than ``name``
    (Users: the address with its Name and role); it defaults to ``name``.
    Leave out an action the row does not offer; a row with none gets an
    empty cell.
    """
    if delete is not None and not confirm:
        raise template.TemplateSyntaxError("table_actions: Delete needs its dialog.")
    if verb not in TABLE_ACTION_VERBS:
        raise template.TemplateSyntaxError("table_actions: unknown verb.")
    return {
        "name": name,
        "edit": edit,
        "delete": delete,
        "confirm": confirm,
        "field": field,
        "verb": verb,
        "icon": TABLE_ACTION_VERBS[verb],
        "confirm_name": confirm_name or name,
    }


@register.inclusion_tag("stewardship/components/disabled-control.html")
def multi_campaign_control(control_id, label):
    """A greyed-out multi-campaign control with the one #145 tip (rule 10).

    Every control whose only purpose is working with more than one campaign
    shows the same tip, defined once in ``campaigns.single_campaign``.
    """
    return {"control_id": control_id, "label": label, "tip": single_campaign.TIP}


@register.inclusion_tag("stewardship/components/menu-link.html", takes_context=True)
def menu_link(context, name, label, query="", href=None, control_id=None):
    """An in-page link to Admin page ``name``, greyed out as the menu greys it.

    Navigation rule 6 links a page only when the viewer may open it now
    (#1242). A menu page follows the viewer's own menu entry (the chrome's
    ``entries``): linked while available, the shared greyed-out control
    with the entry's reason while not, and plain text when the role hides
    the entry. A page in ``admin_navigation.PAGE_REASONS`` is greyed out for
    its reason the same way. ``query`` is appended to the link (a preset
    filter), or ``href`` replaces it whole (a link the view built to that
    page). ``control_id`` names a greyed control uniquely where the page
    shows several links to one page. Without the Admin chrome (a fragment)
    it is an ordinary link. Any other page is refused, so the tag never
    links a page it cannot check.
    """
    if name not in admin_navigation.MENU_NAMES and name not in (
        admin_navigation.PAGE_REASONS
    ):
        raise ValueError(f"menu_link cannot check page {name!r}.")
    chrome = context.get("admin_chrome") or {}
    url, reason = reverse(f"{admin_navigation.NAMESPACE}:{name}"), None
    if name in admin_navigation.PAGE_REASONS and "menu_state" in chrome:
        reason = admin_navigation.PAGE_REASONS[name](chrome["menu_state"])
    elif name in admin_navigation.MENU_NAMES and "entries" in chrome:
        item = chrome["entries"].get(name)
        url, reason = (item.url, item.reason) if item else (None, None)
    if url is not None:
        url = href or url + query
    return {
        "label": label,
        "url": None if reason else url,
        "tip": reason,
        "control_id": control_id or f"{name.replace('_', '-')}-link",
    }


# A {% button %} argument: a name (HTML attribute names keep their hyphens)
# with an optional =value.
BUTTON_ARGUMENT = re.compile(r"([a-z][a-z0-9-]*)(?:=(.+))?\Z", re.S)
# Present or absent by truthiness, never rendered with a value.
BOOLEAN_ATTRIBUTES = {"disabled", "hidden", "formnovalidate", "autofocus"}
# A submit button the server draws disabled says so (data-server-disabled):
# Firefox brings a script-set disabled back on a reload, and ui-v1.js enables
# any disabled submit button without this mark when it starts (#921).
SERVER_DISABLED = " data-server-disabled"


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
    boolean attributes render by truthiness; a submit button's ``disabled``
    comes with ``data-server-disabled`` (see SERVER_DISABLED). A bare name
    (``data-bulk-action``) or a value of True renders as a bare attribute,
    except that an ``aria-*`` attribute always spells its state: True and
    False render as "true" and "false" (ARIA reads a bare aria-pressed as an
    empty, invalid token), and an empty one is left out, so
    ``aria-describedby=reason`` names a reason only while there is one
    (#563). Any other value is escaped, and omitted only when it is None or
    False.
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
        submit = values.get("type") == "submit"
        disabled = " disabled" + (SERVER_DISABLED if submit else "")
        for name, value in values.items():
            if name in ("variant", "class"):
                if classes:
                    parts.append(f' class="{conditional_escape(classes)}"')
                    classes = ""
            elif name == "enabled":
                parts.append("" if value else disabled)
            elif name == "disabled":
                parts.append(disabled if value else "")
            elif name in BOOLEAN_ATTRIBUTES:
                parts.append(f" {name}" if value else "")
            elif name.startswith("aria-") and isinstance(value, bool):
                parts.append(f' {name}="{str(value).lower()}"')
            elif name.startswith("aria-") and value == "":
                continue
            elif value is True:
                parts.append(f" {name}")
            elif value is not None and value is not False:
                parts.append(f' {name}="{conditional_escape(value)}"')
        tag = "a" if self.link else "button"
        label = self.nodelist.render(context)
        return mark_safe(f"<{tag}{''.join(parts)}>{label}</{tag}>")
