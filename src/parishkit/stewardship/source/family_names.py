"""The one rule for naming a Family in reports, pages and email.

ParishSoft's ``mailingName`` is free text that parish staff edit: for some
households it is a single person's given name, so showing it as the Family's
name misleads staff and parishioners alike. The household surname
(``lastName``) is the stable identity. SQL report functions apply the same
precedence inline (see the ``family_name`` expressions in schema/*.sql), and
``stewardship_family_heads_name_v2`` (migration 0194) builds the same
surname-and-heads string as ``family_heads_name``, so the Family codes
directory and the report selections search and order by it.

The salutation order (``heads_salutation_name``: "Andrew and Betty Test") has
no SQL counterpart: nothing searches or sorts by it, so only Python builds it.
``name_placeholders`` turns it into the name placeholders of Family email and
Family pages, so both name the same people (#471).
"""

# Every character Python's str.strip() removes (str.isspace). SQL that builds
# these names trims with this set (snapshot_name_sql binds it), so a name
# built in SQL matches the one built here even with tabs or no-break spaces.
WHITESPACE = (
    " \t\n\x0b\x0c\r\x1c\x1d\x1e\x1f\x85\xa0\u1680"
    + "".join(chr(code) for code in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000"
)


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


def _surname_groups(heads, surname):
    """The heads grouped by last name, for both name orders.

    Returns ``(own, others, alone)``. Heads with a last name are grouped
    under it, ignoring letter case (casefold; the first spelling seen is
    kept), in the order each group's first head appears; a group is
    ``(last name, [first names])``. ``own`` is the group of the Family
    ``surname`` (or None) and ``others`` the other groups. ``alone`` holds
    the full names of heads without a last name (a first name only, or an
    older capture with just ``name``). SQL groups the same way in
    ``stewardship_family_heads_name_v2`` (migration 0194).
    """
    groups = {}  # casefolded last name -> (first spelling, [first names])
    alone = []
    for head in heads:
        first, last = _text(head, "first"), _text(head, "last")
        if last:
            groups.setdefault(last.casefold(), (last, []))[1].append(first)
        elif full := _head_full_name(head):
            alone.append(full)
    own = groups.pop(surname.casefold(), None) if surname else None
    return own, list(groups.values()), alone


def heads_salutation_name(heads, default="", *, surname=""):
    """The heads as a salutation addresses them: "Andrew and Betty Test".

    The rule:

    - Heads are grouped by last name (``_surname_groups``). Each group reads
      as the ``name_series`` of its first names followed by the last name
      once ("Ann, Bob and Cy Lee"). Blank first names add nothing, so a
      group of only surnames is just the surname.
    - The group of the Family ``surname`` (when given) comes first, then the
      other groups in the heads' order: "Maureen Kelly and Linda Thieneman"
      for the Kelly Family even when Linda is the first head (#2320).
    - A head without a last name keeps its full display name as a group of
      its own. These come after every surname group, so a lone first name
      never reads as sharing the next group's surname ("Bob Lee and Cher",
      not "Cher and Bob Lee").
    - The groups are then joined by ``name_series`` again: "Andrew and Betty
      Test and Carol Smith"; "Ann Lee, Bob Ray and Cy Fox".

    Without any usable name the result is ``default``.
    """
    own, others, alone = _surname_groups(heads, surname)
    groups = ([own] if own else []) + others
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
    salutation = heads_salutation_name(heads, family_name, surname=family_name)
    return {
        "family_name": family_name,
        "head_salutation": salutation,
        "family_member_names": salutation,
        "all_family_member_names": heads_salutation_name(
            members, family_name, surname=family_name
        ),
    }


def family_last_name(values):
    """The Family's recorded surname (stripped ``lastName``), or "" if none.

    ``family_display_name`` stands in for a blank one with the mailing name
    or a first name; ``family_heads_name`` must know when it did (#2320).
    """
    return _text(values, "lastName")


def family_heads_name(surname, heads, *, surname_first=True, recorded=True):
    """The Family as staff recognize it: "Squyres, Tracy and Jeff".

    The surname leads so the string sorts by it. When every head with a last
    name shares the Family surname (ignoring letter case), the heads follow
    in their order as first names; a head without a last name, or captured
    before first and last names were kept separately, contributes its full
    display name. Without heads it is just the surname.

    When heads have other last names (#2320), the surnames lead, separated by
    " / ": the Family surname first (when a head has it), then the others in
    the heads' order. After a comma come each surname's first names, in the
    same order and also separated by " / ", each group joined by
    ``name_series``: "Kelly / Thieneman, Maureen and Bob / Linda". Heads
    without a last name follow, in full, as further " / " parts. A surname
    whose heads have no first names leaves no empty part. SQL builds the
    same string in ``stewardship_family_heads_name_v2`` (migration 0194).

    ``recorded`` is False when ``surname`` only stands in for a blank
    ``lastName`` (``family_display_name``'s mailing name, first name or
    default; ``family_last_name``). No head then shares the Family surname,
    so a head with a last name always leads, as when no head shares it:
    "Lee / Fox, Ann / Bo", or "Lee, Ann and Bo". Only when no head has a
    last name does the stand-in lead, as before: "The Lees, Ann and Bo".

    With ``surname_first=False`` the names read naturally, as in a salutation
    or on an envelope (see ``heads_salutation_name``): "Tracy and Jeff
    Squyres", "Tracy Squyres and Jeff Smith". Without heads it is again just
    the surname.
    """
    if not surname_first:
        return heads_salutation_name(heads, surname, surname=surname)
    own, others, alone = _surname_groups(heads, surname if recorded else "")
    if others:
        surnames = ([surname] if own else []) + [last for last, _ in others]
        groups = ([own] if own else []) + others
        parts = [name_series(firsts) for _, firsts in groups] + alone
        leading = " / ".join(surnames)
        names = " / ".join(part for part in parts if part)
        return f"{leading}, {names}" if names else leading
    parts = [
        _text(h, "first") if _text(h, "last") else _head_full_name(h) for h in heads
    ]
    names = name_series(parts)
    return f"{surname}, {names}" if names else surname
