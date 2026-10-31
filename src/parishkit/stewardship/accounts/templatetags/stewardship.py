"""Only typed values reach the canonical display functions; HTML stays escaped."""

import re
from datetime import date, time

from django import template
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import conditional_escape, format_html, strip_tags
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from parishkit.stewardship.accounts import admin_navigation, google_account_help, stages
from parishkit.stewardship.campaigns import single_campaign
from parishkit.stewardship.campaigns.configuration import REMINDER_WORKGROUP_NAME
from parishkit.stewardship.pipeline import Decision
from parishkit.stewardship.source.source_status import source_status_label
from parishkit.stewardship.web import dates, presentation, review_live, stat_tiles
from parishkit.stewardship.web.buttons import portal_classes
from parishkit.stewardship.web.exports import DEFAULT_EXPORT_FORMAT
from parishkit.stewardship.web.review_worklist import clamp_name

register = template.Library()
# A review region's item key, token and seen token (#2174,
# web/review_live.py): the values the server compares a region's read
# with, and (seen) what ui-v1.js compares to tag a redrawn item.
register.filter("live_key", review_live.item_key)
register.filter("live_token", review_live.item_token)
register.filter("live_seen", review_live.seen_token)
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
def stat_tiles_row(labelledby="", label="", tiles_id=""):
    """A stat-tile row's name attribute and toggletip id prefix (#1540).

    Raises (see ``stat_tiles.row_name``) when the row has no name.
    """
    (attribute, value), prefix = stat_tiles.row_name(labelledby, label, tiles_id)
    return {"name": format_html('{}="{}"', attribute, value), "prefix": prefix}


# The direction arrows a comparison line starts its change with ("↑ 12.5%
# more"); the words after them already say the direction.
ARROWS = re.compile("([↑↓→])")


@register.filter
def arrows_hidden(text):
    """``text`` escaped, with each direction arrow hidden from screen readers.

    Stat tile lines such as "Last year on day 3: 30 (↑ 33.3% more)" carry
    the direction in words too, so a screen reader announcing "up arrow"
    only repeats it (#1637). The arrow stays visible.
    """
    return mark_safe(
        ARROWS.sub(r'<span aria-hidden="true">\1</span>', str(conditional_escape(text)))
    )


@register.filter
def underscore_breaks(text):
    """``text`` escaped, with a line-break opportunity after each underscore.

    An identifier such as a System logs event name ("credential_cipher_
    batch_migrated") has no spaces, so it would set its column's minimum
    width; ``<wbr>`` lets it wrap between its words instead (#2280). The
    text, its accessible name and a copy of it are unchanged.
    """
    return mark_safe(str(conditional_escape(text)).replace("_", "_<wbr>"))


@register.filter
def scope_label(scope):
    """A population scope's words, as the Families counted choice says them.

    ``reports.participation.SCOPE_LABELS`` is their one place (#2324), so an
    export's or a report email's page names the population exactly as the
    Response dashboard does; imported here so template loading never pulls
    the reports package in at startup.

    An export that counts no population (a campaign archive's parameters
    carry no ``population_scope``, so ``scope`` is "") is named as the
    export page's earlier ``{% else %}`` branch named every scope but
    "historical", so its page renders as before rather than failing.
    """
    from parishkit.stewardship.reports.participation import SCOPE_LABELS

    return SCOPE_LABELS["historical" if scope == "historical" else "current"]


@register.filter
def share_name(share):
    """A worded share method by its short label, the full one its tooltip.

    ``reports.financial.share_name`` (#2198), imported here so template
    loading never pulls the reports package in at startup.
    """
    from parishkit.stewardship.reports.financial import share_name as name

    return name(share)


@register.simple_tag
def reminder_workgroup_name():
    """The displayed name of the campaign's reminder exclusion setting (#1061).

    Templates take it from its one source, so renaming it is one line.
    """
    return _(REMINDER_WORKGROUP_NAME)


@register.simple_tag
def stage_queued():
    """The Start the load box's waiting sentence (``stages.QUEUED``, #1916).

    Templates take it from its one source, so every page says it alike.
    """
    return str(stages.QUEUED)


@register.simple_tag
def step_tip_side(number, total):
    """Which edge a step bar segment's tooltip is anchored to (#1402).

    "start", "middle" or "end" for the first, middle or last third of the
    bar, the Family step bar's rule (family-v1.js ``paintTrack``), so a
    tooltip at most 60% of the viewport wide never runs off a phone.
    ``number`` counts from 1.
    """
    index = number - 1
    return (
        "start" if index < total / 3 else "middle" if index < 2 * total / 3 else "end"
    )


@register.inclusion_tag("stewardship/components/export-actions.html")
def export_actions(
    *formats,
    name,
    label="",
    disabled="",
    enabled=True,
    describedby="",
    own_order=False,
    tip="",
    tip_more="",
    zone_choice=False,
    zone_value=None,
    sync=False,
):
    """One export's row of small "Export XLSX" … buttons (#1679, #1190).

    Usage: ``{% export_actions "csv" "xlsx" "pdf" name="financial"
    enabled=mutable %}`` inside the export's form. Each button posts its
    own ``format``, so there is no format menu and no separate Download
    button (ui-conventions "Downloaded files", Export actions).

    ``formats`` are the download's offered values. The default
    (``DEFAULT_EXPORT_FORMAT``, XLSX) comes first and the rest keep the
    given order, so no form picks its own default; ``own_order`` keeps the
    given order for a document (the Campaign comparison's PDF summary, the
    follow-up packet). ``name`` prefixes the row's element ids; ``label``
    is the row's group name, telling apart two rows on one page.
    ``disabled`` names one offered format shown but unavailable (Ministry
    requests' PDF above its row limit); ``enabled=False`` makes every button
    unavailable (other campaign work holds exports). ``describedby`` names
    the element ids that describe every button (the line saying why, or a
    complete gate's hint). ``tip`` is the toggletip text closing the row, and
    ``tip_more`` an optional sentence added to it (the Family timeline's
    "It includes the Family code."); a tip is only for what the buttons
    cannot say, such as what the file leaves out, never the file's count,
    scope or columns (ui-conventions "Downloaded files", Export actions).

    Time zone (ui-conventions "Downloaded files", Time zones): the row posts
    the browser's zone in a hidden ``browser_timezone`` field, which
    ``ui-v1.js`` fills (``data-browser-zone``); ``zone_value`` posts that
    value instead (the Response dashboard's drawn zone). ``zone_choice`` is
    for a page that lets the reader view its times in UTC (System logs
    only): a "Times in" menu of UTC and the browser's zone, which ``ui-v1.js``
    adds and selects (``data-browser-timezone``), posted as ``timezone``.
    ``sync`` makes the buttons a ``data-table-sync`` node, redrawn from each
    applied view, for a row whose unavailable format follows the filters.
    """
    if not formats or len(set(formats)) != len(formats):
        raise template.TemplateSyntaxError("export_actions: name each format once.")
    if disabled and disabled not in formats:
        raise template.TemplateSyntaxError("export_actions: unknown disabled format.")
    # A format's button label; any other is its value in capitals.
    labels = {"jsonl": _("JSON Lines")}
    ordered = (
        formats
        if own_order
        else sorted(formats, key=lambda value: value != DEFAULT_EXPORT_FORMAT)
    )
    return {
        "name": name,
        "label": label or _("Export"),
        "buttons": [
            {
                "value": value,
                "label": labels.get(value, value.upper()),
                "enabled": bool(enabled) and value != disabled,
            }
            for value in ordered
        ],
        "describedby": describedby,
        "tip": " ".join(str(part) for part in (tip, tip_more) if part),
        "zone_choice": zone_choice,
        "zone_value": zone_value,
        "sync": sync,
    }


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


def _viewer_allows(context, capability):
    """Whether the page's viewer holds ``capability`` (#1570, #1885).

    Reads the principal the owning view admitted (``request.principal``), as
    the Admin chrome does; Family sessions hold no Admin role. Presentation
    only: the linked page rechecks access itself. A page rendered with its
    request carries it on the context (``RequestContext.request``; no
    context processor adds a ``request`` variable); a fragment rendered from
    a plain dict may pass it as ``request``.
    """
    from parishkit.stewardship.accounts.policy import Principal, allows

    request = getattr(context, "request", None) or context.get("request")
    principal = getattr(request, "principal", None)
    return (
        isinstance(principal, Principal)
        and bool(principal.roles)
        and allows(principal, capability)
    )


def _may_open_timeline(context):
    """Whether the viewer may open a Family's Timeline (#1570): Administrators
    and Staff hold ``CAMPAIGN_REPORT``, Ministry leaders do not."""
    from parishkit.stewardship.accounts.policy import Capability

    return _viewer_allows(context, Capability.CAMPAIGN_REPORT)


@register.simple_tag(takes_context=True)
def user_timeline_url(context, user_id):
    """A user's Timeline address, or "" when the page draws no link (#1885).

    ``user_id`` is the user's stable record id (``AddressRule.record_id``),
    never their address, so the address never reaches a URL. Without one
    (an address that is not a current user) or for a viewer who may not
    open the Timeline (it needs ``MANAGE_USERS``, Administrators only)
    there is no link, and the page keeps what it showed before.
    """
    from parishkit.stewardship.accounts.policy import Capability

    if not user_id or not _viewer_allows(context, Capability.MANAGE_USERS):
        return ""
    return reverse("admin:user_timeline", args=[user_id])


@register.simple_tag(takes_context=True)
def actor_link(context, actor, default=""):
    """The Admin user who acted, by name, linked to their Timeline (#2259).

    Usage: ``{% actor_link row.decided_by %}``. ``actor`` is an
    ``accounts.actor_names.Actor``: its label (the user's Name, else the
    address, else the page's wording for an account that is gone) links to
    the user's Timeline when it names a current user and the viewer may
    open users' Timelines (``actor_names.actor_url``); otherwise the label
    is shown unlinked, so no reader meets a link that would be refused.
    Plain text (a page's own wording for a system or command-line actor)
    is shown as it is. A blank ``actor`` shows ``default``.
    """
    from parishkit.stewardship.accounts.actor_names import actor_html

    if not actor:
        return default
    request = getattr(context, "request", None) or context.get("request")
    return actor_html(actor, getattr(request, "principal", None))


@register.simple_tag(takes_context=True)
def actor_sentence(context, value):
    """A sentence that names an actor, the actor drawn by ``actor_link`` (#2259).

    Usage: ``{{ event.detail }}`` becomes ``{% actor_sentence event.detail %}``.
    An ``accounts.actor_names.ActorSentence`` shows its words with the actor
    by name, linked as ``actor_link`` links it; any other value is shown as
    plain, escaped text.
    """
    from parishkit.stewardship.accounts.actor_names import ActorSentence

    if not isinstance(value, ActorSentence):
        return conditional_escape(value)
    return format_html(
        "{}{}{}", value.before, actor_link(context, value.actor), value.after
    )


@register.simple_tag(takes_context=True)
def family_timeline_url(context, family_id):
    """A Family's Timeline address, or "" when the page draws no link (#1570).

    ``family_id`` is the Family's campaign record id (``FamilyCampaign.pk``);
    without one (a Family with no campaign record yet) or for a viewer who
    may not open the Timeline there is no link. ``family_link`` and Find a
    Family's results share this one decision.
    """
    if not family_id or not _may_open_timeline(context):
        return ""
    return reverse("admin:timeline", args=[family_id])


@register.simple_tag(takes_context=True)
def family_link(context, family_id, name, active=None, clamp=False):
    """A Family's name in an Admin table, linked to its Timeline (#1570).

    Usage: ``{% family_link row.family_id row.display_name active=row.active %}``.
    ``name`` is the shared surname-and-heads name, or None when the current
    ParishSoft data no longer has the Family: the shared "Not in ParishSoft"
    badge then stands alone in its place, never an empty link. A blank name
    is treated the same, defensively: every source gives None for a missing
    Family and a placeholder for a present one with no name, so blank should
    not occur.
    ``family_id`` None draws no link, for a page whose names must not open
    the Timeline (a Testing-only list, or a name already linking its row's
    own page). ``active`` False adds the
    "No longer in this campaign" badge after a known name (#1393), outside the link
    (ui-conventions, #1216). ``clamp`` True wraps a known name to at most
    two lines, then clamps it, whole in its tip (a review work table's
    names, #2124). The name links only when
    ``family_timeline_url`` gives an address, so every table that names a
    Family links it the same way, for the same viewers.
    """
    shown = conditional_escape(name) if name else source_status("missing")
    if name and clamp:
        shown = clamp_name(name)
    href = family_timeline_url(context, family_id)
    if href:
        shown = format_html('<a href="{}">{}</a>', href, shown)
    if name and active is False:
        shown = format_html("{} {}", shown, source_status("left"))
    return shown


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
def setup_test_stages(items):
    """The newest setup test's stage boxes (``setup_mail_views.progress``).

    Test email and Test Slack draw them from the same rows as their Recent
    tests list (#1578), so the page and its polled status region need no
    second copy of the newest test's state.
    """
    from parishkit.stewardship.accounts.setup_mail_views import progress

    return progress(items)


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
# The family directory's ``mode`` (Production or Testing rehearsal) is a
# mode of the whole page, set by the site's mode (#2324; before it, a binary
# choice outside its Filters panel, #1535), so it is not counted as one of
# the panel's filters either.
NOT_FILTERS = frozenset({"sort", "page", "zone", "mode"})


def applied_filters(query, ignore=()):
    """Count a report query's fields that differ from its dataclass defaults.

    A report's query is a frozen dataclass whose defaults are the page's
    unfiltered view (an Additional information default such as "Current
    actionable requests" is the page's normal list, so it does not count).
    Sort, page, zone and mode are left out (``NOT_FILTERS``), as is any field
    named in ``ignore``: one another choice currently overrides.

    As a template filter, ``ignore`` is one comma-separated string
    (``query|applied_filters:"reach,responses"``); it is split into names so
    a field is matched whole, never as a substring ("response" is not
    ignored by "responses").
    """
    if isinstance(ignore, str):
        ignore = tuple(ignore.split(","))
    return sum(
        getattr(query, name) != field.default
        for name, field in query.__dataclass_fields__.items()
        if name not in NOT_FILTERS and name not in ignore
    )


# Also a template filter: an empty report table tells "nothing matches these
# filters" from "nothing to show yet" with the panel's own count (#227), so
# the message and the panel summary never disagree.
register.filter("applied_filters", applied_filters)


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
    #951). A final bare ``open`` word opens the panel by default; only
    the Family directory passes it (Administrator decision, #227). A final
    bare ``fixed`` word draws no disclosure at all: an always-open box
    titled the same way, for a worklist whose filters are never hidden
    (Census changes, Administrator decision, #1682). Otherwise
    the panel starts closed so the report's data
    comes first, and renders open whenever any filter is applied, so an
    applied filter is never hidden behind it, or a filter is refused at its
    field (``filter_error``, #1332), so the error is never hidden either.
    Its summary names how many are applied; that count is a
    data-table-sync node with a stable id, so it follows an in-place Apply
    while the panel and the form keep their elements (ui-v1.js
    ``syncControls``).
    """
    bits = token.split_contents()
    # A trailing bare ``open`` makes the panel open by default, and a
    # trailing bare ``fixed`` makes it an always-open box.
    mode = bits[-1] if bits[-1] in {"open", "fixed"} else ""
    if mode:
        bits.pop()
    if len(bits) not in (2, 3, 4):
        raise template.TemplateSyntaxError(
            "filterpanel takes a query, an optional extra choice, the "
            "query field that choice overrides and an optional final open."
        )
    nodelist = parser.parse(("endfilterpanel",))
    parser.delete_first_token()
    return FilterPanelNode(
        [parser.compile_filter(bit) for bit in bits[1:]], nodelist, mode
    )


class FilterPanelNode(template.Node):
    """Render the panel, open with its count when any filter is applied."""

    def __init__(self, arguments, nodelist, mode=""):
        self.arguments = arguments
        self.nodelist = nodelist
        self.mode = mode

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
        if self.mode == "fixed":
            # The same title and synced count as a heading, so the box is
            # named for screen readers as the disclosure's summary names it.
            return format_html(
                '<section class="filter-panel" data-filter-panel'
                ' aria-labelledby="table-filters-heading">'
                '<h2 id="table-filters-heading">{}: '
                '<span id="table-filters-applied" data-table-sync>{}</span></h2>'
                "{}</section>",
                _("Filters"),
                label,
                self.nodelist.render(context),
            )
        return format_html(
            '<details class="filter-panel" data-filter-panel{}>'
            '<summary>{}: <span id="table-filters-applied" data-table-sync>{}</span>'
            "</summary>{}</details>",
            mark_safe(" open")
            if count or self.mode == "open" or context.get("filter_error")
            else "",
            _("Filters"),
            label,
            # Template output is already escaped and marked safe.
            self.nodelist.render(context),
        )


@register.simple_tag
def whole_dollars(amount):
    """A money cell's amount in whole dollars, its cents kept for screen readers.

    Usage: ``{% whole_dollars row.annual %}`` with a ``MoneyAmount``. A
    narrow table shows "$1,235" (ui-conventions "Table fit and row
    height", #1544); when that rounds away cents, the rounded text is
    hidden from screen readers and the exact "$1,234.50" is read instead,
    so no one hears a figure the downloads do not have.
    """
    shown = amount.whole_display
    if not amount.available or amount.cents % 100 == 0:
        return shown
    return format_html(
        '<span aria-hidden="true">{}</span><span class="visually-hidden">{}</span>',
        shown,
        amount.display,
    )


# One shown amount: an optional minus, "$", grouped dollars and any cents
# ("$99,999.99", "-$12.50", "$1,000"), as MoneyAmount and whole_dollars write
# them.
MONEY_AMOUNT = re.compile(r"-?\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?")


@register.filter
def keep_money(value):
    """Text with each amount of money in it kept on one line (#2258).

    Usage: ``{{ row.answer|keep_money }}``. Each amount ("$99,999.99") is
    wrapped in ``<span class="money">`` (``white-space: nowrap`` in
    ui-v1.css), so main's ``overflow-wrap: anywhere`` never breaks it inside
    a squeezed table cell, while the words around it still wrap
    (ui-conventions "Numbers and money"). Plain text is escaped first;
    text already marked safe keeps its markup.
    """
    text = conditional_escape(value)
    return mark_safe(MONEY_AMOUNT.sub(r'<span class="money">\g<0></span>', text))


@register.filter
def edited_chips(value):
    """A decision's words with "Accepted (edited)" drawn as "Accepted" and
    the shared Edited chip (#2257, UX conventions "Status indicators").

    Usage: ``{{ row.decision_label|edited_chips }}``. The text is escaped
    first; every other word is kept as it is, so a held-decision line or a
    Timeline line reads as before around the chip. A visually hidden comma
    makes a screen reader say "Accepted, edited". Exports and the CLI keep
    the plain decision word, which this page-only filter never changes.
    """
    text = conditional_escape(value)
    edited = conditional_escape(Decision.ACCEPTED_EDITED.label)
    if edited not in text:
        return text
    chip = render_to_string("stewardship/components/edited-chip.html")
    accepted = conditional_escape(Decision.ACCEPTED.label)
    shown = f'{accepted}<span class="visually-hidden">,</span> {chip}'
    return mark_safe(text.replace(edited, shown))


@register.filter
def edited_split(label):
    """A stage chip's word and whether it is the edited decision (#2257).

    Usage: ``{% with stage=row.stage_label|edited_split %}``: ``stage.0``
    is the chip's word ("Accepted" for "Accepted (edited)", else the label
    as it is) and ``stage.1`` is True when the Edited chip follows the
    stage chip, beside it rather than nested inside it.
    """
    if label == Decision.ACCEPTED_EDITED.label:
        return Decision.ACCEPTED.label, True
    return label, False


@register.simple_tag
def filter_mark(error, field, described=""):
    """A report filter field's ``aria-describedby``, and its mark if refused.

    Usage: ``<input id="first-end" …{% filter_mark filter_error "first_end"
    "first-help" %}>`` with the field's query name and the ids that always
    describe it (none, or a space-separated list). When the server refused
    it (``filter_error``, ``web.filter_errors``, #1332) the field is marked
    in error and its line's message id is added to those ids, in the one
    attribute; otherwise only the given ids are written, if any.
    """
    ids = described.split()
    marked = bool(error) and field in error["fields"]
    if marked:
        ids.append(f"{error['line']}_error")
    return format_html(
        "{}{}",
        mark_safe(' aria-invalid="true"') if marked else "",
        format_html(' aria-describedby="{}"', " ".join(ids)) if ids else "",
    )


@register.simple_tag
def filter_line(error, line, sizes=None):
    """A report filter's reserved message line, showing its refusal if any.

    The line is always drawn, hidden and empty until the server refuses a
    field it serves, and keeps the height of the longest message it may
    show (``sizes``, from ``web.filter_errors.line_sizes``), so a refusal
    drawn in place moves nothing (No layout shift). Its list's id,
    ``<line>_error``, is what ``filter_mark`` describes a field by and what
    ui-v1.js ``showFieldErrors`` fills on an in-place Apply.
    """
    shown = bool(error) and error["line"] == line
    return format_html(
        '<div class="field-error-line"><span class="field-error-size" '
        'aria-hidden="true">{}</span><ul class="errorlist" id="{}_error"{}>'
        "<li>{}</li></ul></div>",
        (sizes or {}).get(line, ""),
        line,
        "" if shown else mark_safe(" hidden"),
        error["message"] if shown else "",
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
        "c": _sort_control(table, column, label),
        "css_class": css_class,
        "csrf_token": context.get("csrf_token"),
    }


def _sort_control(table, column, label):
    """What one sort control (table-sort-control.html) draws for ``column``."""
    return {
        "column": column,
        "label": label,
        "state": table.aria_sort(column),
        "descends": table.sort_descends(column),
        "fields": table.heading_fields(column),
        "query": table.heading_query(column),
    }


@register.inclusion_tag("stewardship/table-sort-pair.html", takes_context=True)
def sort_heading_pair(context, table, column, label, second, second_label):
    """A heading over a cell stacking two sortable values (#2124): a name
    and its DUID, each with its own control, the second under the first.

    The heading's ``aria-sort`` is that of whichever of the two the table
    is sorted by. An empty ``second`` names a value the table does not sort
    by (a Ministry request's Member DUID), drawn as plain text.
    """
    first = _sort_control(table, column, label)
    other = (
        _sort_control(table, second, second_label)
        if second
        else {"label": second_label, "state": None}
    )
    return {
        "table": table,
        "first": first,
        "second": other,
        "state": first["state"] or other["state"],
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
    caution=False,
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
    ``caution`` marks the row's action ``data-confirm-caution``, for a
    dialog whose caution is said only for marked rows (confirm-dialog.html
    caution_marked; a scheduled email that has sent, R40b).
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
        "caution": bool(caution),
    }


@register.inclusion_tag("stewardship/components/disabled-control.html")
def multi_campaign_control(control_id, label):
    """A greyed-out multi-campaign control with the one #145 tip (rule 10).

    Every control whose only purpose is working with more than one campaign
    shows the same tip, defined once in ``campaigns.single_campaign``.
    """
    return {"control_id": control_id, "label": label, "tip": single_campaign.TIP}


@register.inclusion_tag(
    "stewardship/components/google-account-help.html", name="google_account_help"
)
def google_account_guidance():
    """The one Google Account guidance for a non-Gmail address (#1593).

    The wording lives in ``accounts.google_account_help``, so every page that
    shows it (and a later leader email, #1592) says exactly the same thing.
    """
    return google_account_help.context()


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


# How many characters of a review work table's value fit its two clamped
# lines at the cell's minimum width (UX conventions, "Review work tables",
# #2124); a longer value, or one of several lines, gets Show all.
CLAMP_CHARS = 28


@register.inclusion_tag("stewardship/components/clamped-value.html")
def clamped_value(value, tip_id):
    """A review work table's value clamped to two lines, with Show all.

    Show all (a toggletip, ``tip_id`` its bubble) is drawn only for a value
    longer than ``CLAMP_CHARS`` or of several lines, which two lines may
    not hold; its bubble floats over the page with the whole value, so
    opening it moves nothing (#2124).
    """
    text = strip_tags(str(value or "")).strip()
    return {
        "value": value,
        "tip_id": tip_id,
        "long": len(text) > CLAMP_CHARS or "\n" in text,
    }
