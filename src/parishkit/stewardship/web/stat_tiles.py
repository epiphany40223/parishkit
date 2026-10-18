"""Admin stat tiles: one builder for every headline figure (#1540).

ui-conventions "Stat tiles": a page's headline figures are drawn by
``components/stat-tiles.html`` from ``StatTile`` values built here, so every
tile formats its value the same way (ui-conventions "Numbers and money") and
an unknown value always reads "—" with a reason, never zero. Views build the
tiles; templates only draw them, and tests can assert tiles without
rendering.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.utils.text import slugify

from parishkit.stewardship.campaigns.domain import Money, Percentage

from .presentation import number, percentage, usd

UNKNOWN = "—"
# A value is drawn on one line at a size chosen by its length (#1537), so a
# fresh answer's wider figure never wraps and makes its row taller. Up to
# LONG characters it is drawn at full size; from LONG at the smaller "long"
# size, and from LONGEST at the smallest, each sized (ui-v1.css) so that
# length fits a desktop tile in wide fonts.
LONG = 10
LONGEST = 14


@dataclass(frozen=True)
class StatTile:
    """One drawn tile: every field is display-ready text.

    ``key`` is stable (the ``data-stat`` hook and the toggletip's id), so it
    must be unique on its page. ``bar`` is ``(count, whole)`` for the share
    bar, or ``None``.
    """

    key: str
    label: str
    text: str
    unknown_reason: str = ""
    details: tuple[str, ...] = ()
    trend: str = ""
    bar: tuple[int, int] | None = None

    @property
    def fit(self):
        """The value's size class for its length, or "" for full size."""
        if len(self.text) >= LONGEST:
            return "stat-value-longest"
        return "stat-value-long" if len(self.text) >= LONG else ""


def formatted(value):
    """``value`` as display text, or ``None`` when it is not known.

    The value's type chooses its format: an exact count or decimal is
    grouped, ``Money`` is USD with cents and a ``Percentage`` is a share.
    A share of nothing (a zero denominator) is unknown, like ``None``.
    """
    if value is None:
        return None
    if isinstance(value, Money):
        return usd(value)
    if isinstance(value, Percentage):
        return percentage(value) if value.value is not None else None
    if type(value) is int or isinstance(value, Decimal):
        return number(value)
    raise TypeError("A stat tile's value must be a count, money or a share.")


def tile(key, label, value, *, unknown_reason="", details=(), trend="", share=None):
    """Build one stat tile, formatting ``value`` by its type.

    An unknown value reads "—" and needs ``unknown_reason``, the plain
    sentence its toggletip says; a known value takes none. ``details`` are
    up to two short explanation lines and ``trend`` an optional comparison
    line in words. ``share`` is ``(count, whole)``: when the whole is above
    zero the tile draws its share bar.
    """
    text = formatted(value)
    if (text is None) != bool(unknown_reason):
        raise ValueError("Exactly the unknown stat tile values carry a reason.")
    details = tuple(line for line in details if line)
    if len(details) > 2:
        raise ValueError("A stat tile has at most two explanation lines.")
    bar = share if share is not None and share[1] > 0 else None
    return StatTile(
        key=key,
        label=str(label),
        text=UNKNOWN if text is None else text,
        unknown_reason=str(unknown_reason),
        details=tuple(str(line) for line in details),
        trend=str(trend),
        bar=bar,
    )


def row_name(labelledby="", label="", tiles_id=""):
    """The accessible name and id prefix of a row of tiles, or an error.

    A row is a list and must be named: ``labelledby`` is the id of the panel
    heading that names it, else ``label`` is its text. A row with neither
    would render an unnamed list, so it raises. The id prefix keeps each
    tile's toggletip id unique when a page draws more than one row: it is
    ``tiles_id`` when given, else the heading id, else the label's slug.
    Returns ``(attribute, prefix)``.
    """
    if not labelledby and not label:
        raise ValueError("A row of stat tiles needs tiles_labelledby or tiles_label.")
    attribute = ("aria-labelledby", labelledby) if labelledby else ("aria-label", label)
    return attribute, str(tiles_id or labelledby or slugify(str(label)))
