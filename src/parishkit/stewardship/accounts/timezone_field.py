"""The one time zone choice for the Parish, campaign and schedule forms (#1286).

The frozen catalog admits every IANA alias (US/Eastern, Canada/Pacific, ...)
so stored documents stay valid, but a new choice is offered by its canonical
name only (America/New_York): an alias works in every SQL conversion only
because the database maps it (``stewardship_timezone_name_v1``), and it reads
as a second, different zone in a list. A stored alias is still shown, as the
current choice, so an unchanged form keeps it: a live campaign's locked
zone, a draft that must match the Parish zone, and configuration history
never change just because a page was saved.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.schema_primitives import (
    canonical_timezone,
    timezone_links,
    timezone_names,
)


def canonical_choices():
    """Every canonical catalog zone, sorted, as (value, label) choices."""
    aliases = timezone_links()
    return [(zone, zone) for zone in sorted(timezone_names()) if zone not in aliases]


class TimezoneSelect(forms.Select):
    """A select that also lists the bound value when it is a stored alias."""

    def optgroups(self, name, value, attrs=None):
        """Add the alias the form holds, labelled with its canonical name."""
        current = next(iter(value or ()), None)
        if current in timezone_links() and current not in dict(self.choices):
            label = _("%(alias)s (older name for %(zone)s)") % {
                "alias": current,
                "zone": canonical_timezone(current),
            }
            self.choices = sorted([*self.choices, (current, label)])
        return super().optgroups(name, value, attrs)


class TimezoneField(forms.ChoiceField):
    """A catalog time zone, offered by canonical names (see the module)."""

    widget = TimezoneSelect

    def __init__(self, **kwargs):
        """Offer the canonical names; the bound value adds a stored alias."""
        super().__init__(choices=canonical_choices, **kwargs)

    def valid_value(self, value):
        """Any catalog name, so a stored alias posted back unchanged is valid."""
        return value in timezone_names()
