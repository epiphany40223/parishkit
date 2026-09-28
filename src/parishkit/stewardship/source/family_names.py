"""The one rule for naming a Family in reports, pages and email.

ParishSoft's ``mailingName`` is free text that parish staff edit: for some
households it is a single person's given name, so showing it as the Family's
name misleads staff and parishioners alike. The household surname
(``lastName``) is the stable identity. SQL report functions apply the same
precedence inline (see the ``family_name`` expressions in schema/*.sql), and
directory_reports.sql builds the same surname-and-heads string as
``family_heads_name`` to search and order the Family codes directory.
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


def name_series(parts):
    """Join names naturally: "A", "A and B", "A, B and C"; blanks are skipped."""
    parts = [part for part in parts if part]
    if len(parts) < 3:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _head_full_name(head):
    """One head's "first last", or the older single display name."""
    parts = (_text(head, "first"), _text(head, "last"))
    return " ".join(part for part in parts if part) or _text(head, "name")


def family_heads_name(surname, heads, *, surname_first=True):
    """The Family as staff recognize it: "Squyres, Tracy and Jeff".

    The surname leads so the string sorts by it. Heads sharing that surname
    contribute their first names; a head of another surname is shown in full
    ("Squyres, Tracy and Jeff Smith"). Without heads it is just the surname.
    Heads captured before first and last names were kept separately
    contribute their full display name.

    With ``surname_first=False`` the names read naturally, as on an envelope:
    heads sharing one surname say it once at the end ("Tracy and Jeff
    Squyres"); otherwise each head's full name is kept ("Tracy Squyres and
    Jeff Smith"). Without heads it is again just the surname.
    """
    if not surname_first:
        split = [(_text(head, "first"), _text(head, "last")) for head in heads]
        lasts = {last for _, last in split}
        if heads and len(lasts) == 1 and "" not in lasts:
            return f"{name_series([first for first, _ in split])} {lasts.pop()}".strip()
        return name_series([_head_full_name(head) for head in heads]) or surname
    parts = []
    for head in heads:
        first, last = _text(head, "first"), _text(head, "last")
        parts.append(first if last == surname else _head_full_name(head))
    names = name_series(parts)
    return f"{surname}, {names}" if names else surname
