"""The one source of the stewardship visual style (#732).

The portal stylesheet (``ui-v1.css``) declares these values as CSS custom
properties on ``:root``, and a unit test (``test_design_tokens.py``) fails if
the stylesheet and this module ever disagree. Email cannot load the
stylesheet, so email components read the same values here and inline them.

``DARK_COLORS`` is the Admin portal's dark colour scheme (#1264; ui-conventions
"Colour scheme"): ``ui-v1.css`` declares it for Admin pages whose scheme is
dark (the Appearance choice, or the operating system's setting), and the same
test checks it. Email and downloaded files
never read it; they stay light.

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
    # The unfilled part of a progress track (setup stepper, Family step bar).
    "track": "#d3dbd9",
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
    # The log level icons that have no status colour of their own (#601);
    # warnings and errors take the status borders above.
    "level-info": "#1d5fbf",
    "level-critical": "#7f0000",
    "level-audit": "#5b3fa0",
    # Chrome details that have a separate value in the dark scheme (#1264):
    # selected text, an unavailable menu entry or control link, the teal
    # hairline round accent-soft boxes, the rule under table headings, a
    # table row under the pointer, and a progress bar's empty track.
    "selection": "#cfe6e2",
    "unavailable": "#646e73",
    "accent-line": "#b9d7d2",
    "rule-strong": "#c9d1cf",
    "row-hover": "#f8faf9",
    "progress-track": "#e3e8e7",
}

# The Admin portal's dark scheme (#1264), with the same keys as COLORS. Text
# keeps 4.5:1 on every surface, and control borders, the focus ring and the
# status icons 3:1 (test_design_tokens.py checks both schemes). The accent
# is a light teal here, so a primary button's --paper label (now dark) keeps
# its contrast on it.
DARK_COLORS = {
    "ink": "#e4eaeb",
    "muted": "#a8b5ba",
    "page": "#121a1d",
    "paper": "#1b2529",
    "paper-soft": "#222e33",
    "border": "#34434a",
    "track": "#3b4b52",
    "control-border": "#80929a",
    "accent": "#5cc0b2",
    "accent-strong": "#86d5ca",
    "accent-soft": "#173c38",
    "gold": "#d2a54e",
    "link": "#84bdf0",
    "focus": "#f0a34a",
    "warning-bg": "#2f2611",
    "warning-border": "#d09a2c",
    "warning-ink": "#f2c66e",
    "error-bg": "#3b1c19",
    "error-border": "#f07a6a",
    "error-ink": "#ffb3a7",
    "level-info": "#7eaaf2",
    "level-critical": "#ff8f86",
    "level-audit": "#bba5ee",
    "selection": "#245a53",
    "unavailable": "#8d9ba1",
    "accent-line": "#2f6a62",
    "rule-strong": "#45555c",
    "row-hover": "#202b30",
    "progress-track": "#34434a",
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
