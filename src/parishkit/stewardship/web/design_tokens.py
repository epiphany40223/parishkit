"""The one source of the stewardship visual style (#732).

The portal stylesheet (``ui-v1.css``) declares these values as CSS custom
properties on ``:root``, and a unit test (``test_design_tokens.py``) fails if
the stylesheet and this module ever disagree. Email cannot load the
stylesheet, so email components read the same values here and inline them.

Only add a token when two places need the same value. Every text and
background pair must keep WCAG AA contrast (see the note at the top of
``ui-v1.css``); re-check it when changing a colour.
"""

# Colours, keyed by their CSS custom property name without the leading "--".
COLORS = {
    "ink": "#1b2a31",
    "muted": "#4f5e66",
    "page": "#f6f5f1",
    "paper": "#fff",
    "paper-soft": "#f3f6f5",
    "border": "#dfe3e2",
    "control-border": "#7a8a90",
    "accent": "#115e56",
    "accent-strong": "#0c4a44",
    "accent-soft": "#e4f0ee",
    "gold": "#b5862e",
    "link": "#155a8a",
    "focus": "#b45309",
    "warning-bg": "#fdf5e1",
    "warning-border": "#a86f00",
    "warning-ink": "#7a4f00",
    "error-bg": "#fcebe8",
    "error-border": "#b42318",
    "error-ink": "#8f1d12",
}

# Corner radii: panels and cards, then controls, notices and buttons.
RADII = {"radius": ".75rem", "radius-small": ".5rem"}

FONTS = {
    "serif": (
        '"Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", '
        "Georgia, serif"
    ),
    "mono": "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
}
# The portal's body text (the :root font shorthand). Mail programs ignore
# system-ui, so email starts from fonts every client has.
SANS = 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
EMAIL_SANS = "Arial, Helvetica, sans-serif"

# Button shape, shared by every variant: the portal's "button, .button" rule
# reads these custom properties.
BUTTON = {
    "button-border-width": "2px",
    "button-padding": ".6rem 1.25rem",
    "button-weight": "650",
    "button-min-height": "44px",
}

# Every custom property ui-v1.css declares on :root from this module.
CSS_PROPERTIES = {**COLORS, **RADII, **FONTS, **BUTTON}

# The rem base for email, which has no root font size to inherit.
REM_PX = 16


def px(length):
    """A CSS length from this module ("2px", ".5rem") in whole email pixels."""
    if length.endswith("rem"):
        return round(float(length[:-3]) * REM_PX)
    if length.endswith("px"):
        return int(length[:-2])
    raise ValueError(f"not a px or rem length: {length!r}")


def hex6(color):
    """A colour token as six-digit hex, which older mail programs need."""
    digits = color.lstrip("#")
    if len(digits) == 3:
        digits = "".join(digit * 2 for digit in digits)
    return "#" + digits.lower()
