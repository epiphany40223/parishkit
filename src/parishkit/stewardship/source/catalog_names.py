"""The one rule for showing a ParishSoft Ministry or fund name on pages and forms.

Parish staff can rename a Ministry or fund in ParishSoft to anything: blank,
very long, or holding control characters. The source loader stores names as
ParishSoft sent them (up to its generic 8192-character bound) so snapshot
payloads and their digests stay a faithful copy of the provider. Names are
therefore repaired here, when they are read for display, rather than at load.

A repaired name never hides or drops a Ministry. Visibility stays a question of
catalog presence, local activity and campaign selection only, exactly as the
SQL visibility guard (``stewardship_response_ministry_visible_v1`` in
schema/guards.sql) decides it, so one odd name cannot make Python and SQL
disagree about which Ministries a Family may answer for.
"""

import hashlib
import logging
import unicodedata

from parishkit.stewardship.observability import Event, emit

# The Family form's longest Ministry label (part of the form digest).
MAX_MINISTRY_LABEL = 512
ELLIPSIS = "\u2026"
# (DUID, name digest) pairs already warned about in this process. The form and
# Admin catalog pages read names on every request, so without this one bad
# name would log thousands of identical lines. The set is bounded; clearing it
# when full only means a still-bad name is reported once more.
_WARNED = set()
_WARNED_LIMIT = 1024


def _usable(name):
    """Whether a raw name already met the form's label rule after NFC/trim."""
    if type(name) is not str:
        return False
    name = unicodedata.normalize("NFC", name.strip())
    return (
        bool(name)
        and len(name) <= MAX_MINISTRY_LABEL
        and not any(unicodedata.category(char).startswith("C") for char in name)
    )


def clean_source_name(name, fallback):
    """Return a safe display name and whether it had to be repaired.

    A name the form could already show is returned exactly as before this
    rule existed: NFC-normalized and trimmed, with inner spacing untouched,
    so deploying the rule cannot change any valid label or the projection
    digests built from it (which would force every Family to reconfirm).

    Only an unusable name is repaired: control whitespace (tab, newline) and
    other whitespace such as NBSP become spaces, every other C* character is
    dropped (control, format such as ZWJ/ZWNJ and bidi marks, unassigned),
    spacing is collapsed and trimmed, and a result longer than
    ``MAX_MINISTRY_LABEL`` is cut and ends in an ellipsis. A name that is
    blank, missing or not text, or empty after that, becomes ``fallback``.
    """
    if _usable(name):
        return unicodedata.normalize("NFC", name.strip()), False
    text = unicodedata.normalize("NFC", name) if type(name) is str else ""
    text = "".join(
        " " if char.isspace() else char
        for char in text
        if char.isspace() or not unicodedata.category(char).startswith("C")
    )
    text = " ".join(text.split())
    if len(text) > MAX_MINISTRY_LABEL:
        text = text[: MAX_MINISTRY_LABEL - 1].rstrip() + ELLIPSIS
    return text or fallback, True


def clean_ministry_name(duid, name):
    """``clean_source_name`` with the "Ministry <DUID>" fallback."""
    return clean_source_name(name, f"Ministry {duid}")


def fund_display_name(duid, name):
    """A safe fund display name, "Fund <DUID>" when blank or missing.

    Fund names appear only on Admin catalog pages (campaign settings and
    first-campaign setup), so a repair there is not separately logged.
    """
    return clean_source_name(name, f"Fund {duid}")[0]


def _name_digest(name):
    """A private fingerprint of a raw name, so a new bad rename warns again."""
    return hashlib.sha256(repr(name).encode("utf-8", "surrogatepass")).hexdigest()


def ministry_display_name(duid, name):
    """A safe display name, logging a DUID-only warning when it was repaired.

    The warning names only the Ministry's DUID, never its name, and goes to
    the process log (not the portal's System logs page). It is emitted once
    per process for each (DUID, name) pair.
    """
    text, repaired = clean_ministry_name(duid, name)
    key = (duid, _name_digest(name))
    if repaired and key not in _WARNED:
        if len(_WARNED) >= _WARNED_LIMIT:
            _WARNED.clear()
        _WARNED.add(key)
        emit(
            Event.SOURCE_MINISTRY_NAME_REPAIRED,
            level=logging.WARNING,
            ministry_duid=duid,
        )
    return text
