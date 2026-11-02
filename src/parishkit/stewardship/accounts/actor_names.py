"""Who did something in the Admin portal, by name, for staff pages (#2259).

Every staff-facing Admin page that names the Admin user who acted (a
reviewer's decision, a follow-up's revision, a System log's actor, an
Home's security events) shows the user's Users-page Name, in its "Last, first"
form ("Administrator, IT"), or the account's address when the user has no
Name, drawn by the shared ``{% actor_link %}`` tag
(``accounts/templatetags/stewardship.py``). The tag links it to that user's
Timeline (``admin:user_timeline``) for a reader who may open it
(``MANAGE_USERS``, Administrators only), so a page never offers a link that
would only be refused (ui-conventions, "Admin users who acted").

The actor a record keeps is a Google account (``PortalUser.pk``), while the
Name and the Timeline's id belong to the current configuration's user row
(``AddressRule``, matched by address in lower case, as Sent mail matches a
recipient, ``jobs.delivery_reads.address_users``). ``actor_names`` reads a
whole page's actors in one query, never one per row, so a table's query
count does not grow with its rows.

An account the portal no longer has keeps the page's existing wording
(``FORMER``), and an account whose address is no longer a current user keeps
its address; neither is linked. System and command-line actors never reach
here: their pages word them as before. Downloads (CSV, PDF, spreadsheets)
and ``pk-admin`` output keep the address, which is machine-readable and
stable, so they read ``Actor.email``, never the label.

This module stays free of view imports so pages in any app can use it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.db.models import OuterRef, Subquery
from django.db.models.functions import Lower
from django.utils.safestring import mark_safe

# Who acted when the account is gone, as every review page already says it.
FORMER = "Former portal user"


@dataclass(frozen=True)
class Actor:
    """One acting account as a page shows it.

    ``label`` is the user's Name, else the address (``email``), else the
    page's wording for an account that is gone. ``user_id`` is the current
    user's stable record id (``AddressRule.record_id``) that their Timeline
    address carries, or None when there is no such user (no link).
    ``str()`` is the label, so a template that prints the value plainly
    still shows the name.
    """

    label: str
    email: str = ""
    user_id: UUID | None = None

    def __str__(self):
        """The shown words: the Name, the address or the fallback wording.

        ``str()`` again, so a lazily translated fallback (a page's own
        wording, ``gettext_lazy``) still gives text.
        """
        return str(self.label)


def gone(label=FORMER):
    """An actor the portal no longer has: its wording, no address, no link."""
    return Actor(str(label))


def user_annotations(address):
    """Subqueries for an ORM read: the current user row of ``address``.

    ``address`` is an expression for the acting account's address (an
    ``OuterRef`` to ``PortalUser.email``, or a subquery giving it). Returns
    ``{"actor_user_id": …, "actor_user_name": …}`` to ``annotate`` a query
    with, so a page that reads its actor in its own statement adds none;
    ``from_parts`` builds the ``Actor``. ``address_rule_identity`` keeps
    each subquery one row.
    """
    from .policy_models import AddressRule
    from .runtime_models import SystemConfiguration

    active = SystemConfiguration.objects.values("active_configuration_id")[:1]
    users = (
        AddressRule.objects.filter(
            configuration_id=Subquery(active), role__isnull=False
        )
        .annotate(lowered=Lower("email"))
        .filter(lowered=Lower(address))
    )
    return {
        "actor_user_id": Subquery(users.values("record_id")[:1]),
        "actor_user_name": Subquery(users.values("name")[:1]),
    }


def from_parts(email, user_id, name, fallback=FORMER):
    """The ``Actor`` for an address and its user row's id and Name (or None).

    No address means the account is gone: ``fallback``, unlinked.
    """
    if not email:
        return gone(fallback)
    return Actor((name or "").strip() or email, email, user_id)


def actor_names(identities, *, fallback=FORMER):
    """Map each ``PortalUser`` id (as text) in ``identities`` to its ``Actor``.

    One query for all of them, none when ``identities`` names none: the
    accounts, each with the current configuration's user row for its
    address (``user_annotations``). An id the portal no longer has is
    absent; ``actor_of`` gives it ``fallback``. Accepts UUIDs or their
    text, and ignores None and blanks.
    """
    from .policy_models import PortalUser

    wanted = set()
    for value in identities:
        try:
            wanted.add(UUID(str(value)))
        except ValueError:
            continue
    if not wanted:
        return {}
    rows = (
        PortalUser.objects.filter(pk__in=wanted)
        .annotate(**user_annotations(OuterRef("email")))
        .values_list("id", "email", "actor_user_id", "actor_user_name")
    )
    return {
        str(key): from_parts(email, user_id, name, fallback)
        for key, email, user_id, name in rows
    }


def address_actors(addresses):
    """Map each address in ``addresses`` to its ``Actor``, for SQL-joined reads.

    For a read whose SQL already joined the acting account's address
    (``stewardship_portal_user.email``): the same current user row
    ``actor_names`` finds, by the same lower-cased match, in one query for
    the page, none when there is no address. Every address gets an
    ``Actor``: one that is no current user keeps its address, unlinked.
    """
    from .policy_models import AddressRule
    from .runtime_models import SystemConfiguration

    wanted = {address for address in addresses if address}
    if not wanted:
        return {}
    active = SystemConfiguration.objects.values("active_configuration_id")[:1]
    users = {
        lowered: (record_id, (name or "").strip())
        for lowered, record_id, name in AddressRule.objects.filter(
            configuration_id=Subquery(active), role__isnull=False
        )
        .annotate(lowered=Lower("email"))
        .filter(lowered__in={address.lower() for address in wanted})
        .values_list("lowered", "record_id", "name")
    }
    found = {}
    for address in wanted:
        user_id, name = users.get(address.lower(), (None, ""))
        found[address] = Actor(name or address, address, user_id)
    return found


def actor_url(value, principal):
    """The acting user's Timeline address for ``principal``, or "".

    A link only for an ``Actor`` that names a current user, and only for a
    reader with an Admin role who may open users' Timelines
    (``MANAGE_USERS``); the Timeline rechecks access itself. The user's
    record id, never the address, is what the URL carries.
    """
    from django.urls import reverse

    from .policy import Capability, Principal, allows

    user_id = getattr(value, "user_id", None)
    if not (
        user_id
        and isinstance(principal, Principal)
        and principal.roles
        and allows(principal, Capability.MANAGE_USERS)
    ):
        return ""
    return reverse("admin:user_timeline", args=[user_id])


def actor_html(value, principal):
    """The actor as safe HTML: its label, linked when ``actor_url`` gives one.

    The one rendering every staff page uses, through ``{% actor_link %}``
    or, for HTML a view builds itself, directly. Plain text passes through
    escaped and unlinked.
    """
    from django.utils.html import conditional_escape, format_html

    label = conditional_escape(str(value or ""))
    href = actor_url(value, principal)
    return format_html('<a href="{}">{}</a>', href, label) if href else label


def actor_text(value):
    """How a download or ``pk-admin`` names an actor: its address (#2259).

    An ``Actor`` gives its address, else its label (an account that is gone
    keeps the page's wording, as the files always said it); plain text and
    blanks pass through, so a column that holds either reads the same.
    """
    if isinstance(value, Actor):
        return value.email or value.label
    return value or ""


def actor_of(names, identity, fallback=FORMER):
    """``identity``'s ``Actor`` from ``actor_names``'s map, else ``fallback``."""
    return names.get(str(identity)) or gone(fallback)


# The current configuration's user row for a joined account's address, as
# SQL for a read that already joins ``stewardship_portal_user`` (alias
# ``{user}``): the same lower-cased match as ``actor_names``, so a page that
# reads its actors in its own statement adds none. ``{alias}.record_id`` is
# the user's Timeline id and ``{alias}.name`` their Name, both NULL when the
# address is no current user. ``address_rule_identity`` keeps it one row.
ACTOR_USER_JOIN = """LEFT JOIN stewardship_address_rule {alias}
    ON {alias}.configuration_id=(SELECT active_configuration_id
        FROM stewardship_system_configuration)
    AND {alias}.role IS NOT NULL AND lower({alias}.email)=lower({user}.email)"""

# Stands in for the actor while a translated sentence is split around it.
WHO = "\x00who\x00"


class ActorSentence(str):
    """A sentence naming an actor, as text and as its three parts (#2259).

    As text (a download, ``pk-admin``, a sort) it names the actor by
    address (``actor_text``), as it always did. ``before``, ``actor`` and
    ``after`` let a page draw the actor with ``{% actor_sentence %}``, by
    name and linked, inside the same words. ``markup`` is the page's own
    safe-HTML form of the sentence (also holding ``WHO`` once) for a line
    whose words carry markup, such as a pledge Edit's short labels (#2198);
    the page's ``before`` and ``after`` then come from it, while the text
    stays in full labels.
    """

    def __new__(cls, sentence, actor, markup=None):
        """Split ``sentence`` (holding ``WHO`` once) around ``actor``."""
        before, _who, after = sentence.partition(WHO)
        value = super().__new__(cls, before + actor_text(actor) + after)
        value.before, value.actor, value.after = before, actor, after
        if markup:
            drawn_before, _who, drawn_after = markup.partition(WHO)
            value.before, value.after = mark_safe(drawn_before), mark_safe(drawn_after)
        return value


def match_windows(identities):
    """The verified-address matches of these accounts, by account id (#2343).

    One query for all of them, none when ``identities`` names none; each
    account's matches, standing or ended, oldest first, as ``through``
    reads them.
    """
    from .policy_models import AddressMatch

    wanted = {str(value) for value in identities if value}
    if not wanted:
        return {}
    found = {}
    for row in (
        AddressMatch.objects.filter(portal_user_id__in=wanted)
        .order_by("matched_at", "id")
        .values(
            "portal_user_id",
            "email",
            "user_record_id",
            "sign_in_email",
            "matched_at",
            "ended_at",
        )
    ):
        found.setdefault(str(row["portal_user_id"]), []).append(row)
    return found


def through(actor, matches, at):
    """How a matched Google account's entry at ``at`` names its actor.

    Inside one of its match windows (from the match to its end) it reads
    "_user address_ through _sign-in address_", from that match record,
    linked to the user's Timeline; outside every window, when the account
    acts as a user's address only now, it reads its sign-in address. So the
    label of an entry never changes when a match ends (admin-portal
    specification, "Verified-address matches", Where it shows). Any other
    actor is returned as it is.
    """
    from django.utils.translation import gettext as _

    if not matches or actor is None:
        return actor
    for match in matches:
        if match["matched_at"] <= at and (
            match["ended_at"] is None or at <= match["ended_at"]
        ):
            label = _("%(user)s through %(account)s") % {
                "user": match["email"],
                "account": match["sign_in_email"],
            }
            return Actor(label, match["email"], match["user_record_id"])
    if actor.email in {match["email"] for match in matches}:
        address = matches[-1]["sign_in_email"]
        return Actor(address, address)
    return actor
