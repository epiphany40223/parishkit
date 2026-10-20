"""Admin tabs: several views of one subject on one page (#1712).

ui-conventions "Tabs": a page draws its tab row and panels with
``components/tabs.html`` from the ``TabSet`` that ``tab_set`` builds here.
The chosen tab is the address's bare ``tab`` parameter; ``tab_set`` reads it,
falls back to the first available tab when it names none (or an unavailable
one), and gives each tab its link: the page's own path with only the
parameters the page carries and that tab. Views build the set; the template
only draws it, and ``ui-v1.js`` switches tabs in place.
"""

import re
from dataclasses import dataclass, replace
from urllib.parse import urlencode

# The address parameter that names the chosen tab. It is bare (not prefixed
# per page) because a page has at most one tab row.
PARAMETER = "tab"
# A key becomes part of element ids and the address, so it is a short slug.
KEY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


@dataclass(frozen=True)
class Tab:
    """One tab: its stable ``key``, its visible ``label`` and its panel.

    ``template`` is the panel's template (a name, or a compiled template),
    drawn with the page's context only while this tab is chosen. ``count``
    is an optional number shown after the label. ``unavailable`` is the
    plain-language reason a tab that applies cannot be opened now; an
    unavailable tab has no panel. ``href`` and ``selected`` (whether this is
    the chosen tab) are filled in by ``tab_set``.
    """

    key: str
    label: str
    template: str = ""
    count: int | None = None
    unavailable: str = ""
    href: str = ""
    selected: bool = False

    def __post_init__(self):
        """Refuse a key that cannot be an id, or an available tab with no panel."""
        if not KEY.fullmatch(self.key):
            raise ValueError(f"Tab key {self.key!r} must be a lower-case slug.")
        if not self.unavailable and not self.template:
            raise ValueError(f"Available tab {self.key!r} needs a panel template.")


@dataclass(frozen=True)
class TabSet:
    """The tabs a page draws, with the one chosen for this address."""

    tabs: tuple[Tab, ...]
    selected: str

    @property
    def default(self):
        """The tab an address without ``tab`` shows: the first available one."""
        return next(tab.key for tab in self.tabs if not tab.unavailable)

    @property
    def chosen(self):
        """The chosen ``Tab`` itself."""
        return next(tab for tab in self.tabs if tab.key == self.selected)


def tab_set(path, parameters, tabs, *, carry=()):
    """The page's ``TabSet`` for an address's ``parameters`` (``request.GET``).

    ``path`` is the page's own path and ``carry`` the ``(name, value)``
    pairs every tab's link keeps (what identifies the page's subject or mode,
    never a panel's filters). At least one tab must be available and keys
    must be unique.
    """
    tabs = tuple(tabs)
    keys = [tab.key for tab in tabs]
    if len(set(keys)) != len(keys):
        raise ValueError("Tab keys must be unique.")
    available = [tab.key for tab in tabs if not tab.unavailable]
    if not available:
        raise ValueError("At least one tab must be available.")
    asked = parameters.get(PARAMETER, "")
    selected = asked if asked in available else available[0]
    carried = [(name, value) for name, value in carry if name != PARAMETER]
    linked = tuple(
        replace(
            tab,
            href=f"{path}?{urlencode([*carried, (PARAMETER, tab.key)])}",
            selected=tab.key == selected,
        )
        for tab in tabs
    )
    return TabSet(linked, selected)
