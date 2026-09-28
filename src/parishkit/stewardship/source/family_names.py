"""The one rule for naming a Family in reports, pages and email.

ParishSoft's ``mailingName`` is free text that parish staff edit: for some
households it is a single person's given name, so showing it as the Family's
name misleads staff and parishioners alike. The household surname
(``lastName``) is the stable identity. SQL report functions apply the same
precedence inline (see the ``family_name`` expressions in schema/*.sql).
"""


def _text(values, key):
    """A stripped source string, or an empty string for missing/non-text."""
    value = values.get(key)
    return value.strip() if isinstance(value, str) else ""


def family_display_name(values, default=""):
    """Return lastName, else mailingName, else "first last", else ``default``."""
    full = " ".join(
        part for part in (_text(values, "firstName"), _text(values, "lastName")) if part
    )
    return _text(values, "lastName") or _text(values, "mailingName") or full or default
