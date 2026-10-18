"""Ministry requests' Leaders column (#1594).

Staff and Administrators see, for each Ministry in By Ministry, whether it
has a ParishSoft leader who can sign in (reports specification, "Ministry
change summary"). The web login reads no Member, contact or roster record:

- ``read_coverage`` reads per-Ministry counts through the
  ``stewardship_ministry_leader_coverage_v1`` definer (migration 0168),
  which answers only a live Staff or Administrator Admin session, takes no
  address or Ministry and records its own count-only audit event;
- ``leader_status`` turns one Ministry's counts into the column's status,
  without I/O, so the page and ``report ministry`` show the same words.
"""

import json
from dataclasses import dataclass

from django.db import DatabaseError, connection, transaction
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.web.presentation import number

# How the definer words its session refusal (SQLSTATE 42501, which a login
# without EXECUTE also gets, so the words tell the two apart).
REFUSAL = "Leader coverage requires a live Staff or Administrator session"
REFUSED_STATE = "42501"

# The column's words for each state that is not a count (the specification's
# table); a Ministry whose leaders' users signed in reads "N signed in".
WORDS = {
    "no_leader": _("No leader"),
    "no_user": _("No user yet"),
    "not_signed_in": _("Not signed in yet"),
}


class CoverageRefused(PermissionError):
    """The definer refused the session: not a live Staff or Administrator's."""


@dataclass(frozen=True)
class LeaderStatus:
    """One Ministry's Leaders cell.

    ``state`` is ``no_leader``, ``no_user``, ``not_signed_in`` or
    ``signed_in``; ``signed_in`` counts its leaders' users who have signed
    in. ``working`` is whether a leader can work its requests, which picks
    the status indicator's look (Working or Warning).
    """

    state: str
    signed_in: int

    @property
    def working(self):
        """Whether at least one leader's user has signed in."""
        return self.state == "signed_in"

    @property
    def word(self):
        """The cell's text, in the reader's language."""
        if self.working:
            return ngettext(
                "%(count)s signed in", "%(count)s signed in", self.signed_in
            ) % {"count": number(self.signed_in)}
        return WORDS[self.state]

    def as_json(self):
        """The command line's form: the state and the signed-in count."""
        return {"state": self.state, "signed_in": self.signed_in}


def leader_status(counts):
    """One Ministry's status from the definer's counts (or None: no leader).

    The states are checked in the order a Ministry gains them: a leader in
    ParishSoft, then a user for one of their addresses, then a sign-in.
    """
    counts = counts or {}
    signed_in = int(counts.get("signed_in", 0))
    if not int(counts.get("leaders", 0)):
        state = "no_leader"
    elif not int(counts.get("users", 0)):
        state = "no_user"
    elif not signed_in:
        state = "not_signed_in"
    else:
        state = "signed_in"
    return LeaderStatus(state=state, signed_in=signed_in)


def read_coverage(session_key):
    """Each Ministry's leader counts, keyed by Ministry DUID, for this session.

    Calls the definer in its own savepoint (it writes its audit event), so
    it must run on a writable connection, never inside a guarded read-only
    response. A session the definer refuses raises ``CoverageRefused``; any
    other database failure, a login without EXECUTE included, propagates.
    """
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.stewardship_ministry_leader_coverage_v1(%s, %s)::text",
                [session_key, current_correlation()],
            )
            (value,) = cursor.fetchone()
    except DatabaseError as error:
        cause = error.__cause__
        if getattr(cause, "sqlstate", None) == REFUSED_STATE and (
            getattr(getattr(cause, "diag", None), "message_primary", None) == REFUSAL
        ):
            raise CoverageRefused("Leader coverage refused.") from None
        raise
    return {
        int(row["ministry"]): row
        for row in json.loads(value).get("ministries", [])
        if isinstance(row, dict) and row.get("ministry")
    }
