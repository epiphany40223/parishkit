"""The one rule for naming a Family in reports, pages and email.

ParishSoft's ``mailingName`` is free text that parish staff edit: for some
households it is a single person's given name, so showing it as the Family's
name misleads staff and parishioners alike. The household surname
(``lastName``) is the stable identity. SQL report functions apply the same
precedence inline (see the ``family_name`` expressions in schema/*.sql), and
directory_reports.sql builds the same surname-and-heads string as
``family_heads_name`` to search and order the Family codes directory.

The salutation order (``heads_salutation_name``: "Andrew and Betty Test") has
no SQL counterpart: nothing searches or sorts by it, so only Python builds it.
``name_placeholders`` turns it into the name placeholders of Family email and
Family pages, so both name the same people (#471).
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


def heads_salutation_name(heads, default=""):
    """The heads as a salutation addresses them: "Andrew and Betty Test".

    The rule, in the heads' order except as noted:

    - Heads with a last name are grouped under it, ignoring letter case (the
      first spelling seen is used); a group is placed where its first head
      appears. Each group reads as the ``name_series`` of its first names
      followed by the last name once ("Ann, Bob and Cy Lee"). Blank first
      names add nothing, so a group of only surnames is just the surname.
    - A head without a last name (a first name only, or an older capture with
      just ``name``) keeps its full display name as a group of its own. These
      come after every surname group, so a lone first name never reads as
      sharing the next group's surname ("Bob Lee and Cher", not "Cher and Bob
      Lee").
    - The groups are then joined by ``name_series`` again: "Andrew and Betty
      Test and Carol Smith"; "Ann Lee, Bob Ray and Cy Fox".

    Without any usable name the result is ``default``.
    """
    groups = []  # (last name, [first names]) in order of first appearance
    by_last = {}
    alone = []  # full names of heads without a last name
    for head in heads:
        first, last = _text(head, "first"), _text(head, "last")
        if last:
            key = last.casefold()
            if key not in by_last:
                by_last[key] = []
                groups.append((last, by_last[key]))
            by_last[key].append(first)
        elif full := _head_full_name(head):
            alone.append(full)
    parts = [f"{name_series(firsts)} {last}".strip() for last, firsts in groups]
    return name_series(parts + alone) or default


def name_placeholders(family_name, heads, members):
    """The Family name placeholders, built one way for email and Family pages.

    ``heads`` are the Family's active heads of household and ``members`` every
    active, listed (not deceased) Member, each as ``{"first", "last"}`` in
    DUID order. ``head_salutation`` addresses the heads ("Andrew and Betty
    Test"); ``family_member_names`` is its older name, kept for stored
    templates; ``all_family_member_names`` names every Member ("Andrew, Betty
    and Cy Test"). A Family without usable head (or Member) names is addressed
    by ``family_name`` instead, so no greeting is ever blank (#471).
    """
    salutation = heads_salutation_name(heads, family_name)
    return {
        "family_name": family_name,
        "head_salutation": salutation,
        "family_member_names": salutation,
        "all_family_member_names": heads_salutation_name(members, family_name),
    }


def family_heads_name(surname, heads, *, surname_first=True):
    """The Family as staff recognize it: "Squyres, Tracy and Jeff".

    The surname leads so the string sorts by it. Heads sharing that surname
    contribute their first names; a head of another surname is shown in full
    ("Squyres, Tracy and Jeff Smith"). Without heads it is just the surname.
    Heads captured before first and last names were kept separately
    contribute their full display name.

    With ``surname_first=False`` the names read naturally, as in a salutation
    or on an envelope (see ``heads_salutation_name``): "Tracy and Jeff
    Squyres", "Tracy Squyres and Jeff Smith". Without heads it is again just
    the surname.
    """
    if not surname_first:
        return heads_salutation_name(heads, surname)
    parts = []
    for head in heads:
        first, last = _text(head, "first"), _text(head, "last")
        parts.append(first if last == surname else _head_full_name(head))
    names = name_series(parts)
    return f"{surname}, {names}" if names else surname
