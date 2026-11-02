"""Sign-in through another verified address (#2343): the decisions and writes.

A Google account whose sign-in address is no user's may act as the one user
whose address Google verified for that account, once the person lets the
portal read the account's addresses (the second sign-in step). The design is
the admin-portal specification's "Sign-in through another verified address",
"Verified-address outcomes" and "Verified-address matches", and the data
specification's "Administration user and policy" (what the sign-in refresh
writes) and "Verified-address match records" (the SQL guard, which decides
every match write again).

Everything here runs only while the web's ``alternate_address_sign_in``
setting is on (``enabled``), except ``usable``, which ``current_principal``
asks for an account that acts as another address, so that turning the
setting off cuts a matched session at its next request.

Three rules hold for every match write (the notes on #2343 from the reviews
of #2367):

- the users' ordering lock, ``pg_advisory_xact_lock(736220, 1)``, is taken
  before ``select_for_update`` on any portal user, because configuration
  activation takes that lock first and then share-locks a portal user row;
- no write names an actor (never ``mutate()``, which sets ``actor_id``): the
  guard refuses a match insert with one and keeps ``actor_id`` unchanged on
  every update, and the audit rows carry the attribution;
- a re-check is decided here, in Python, for an enabled account whose
  ``sign_in_email`` is still the match's: SQL does not re-check either.

The People API's list of addresses lives only in this process's memory for
the one request; only the matched address is kept, as part of the match.
"""

import logging
import time
from dataclasses import dataclass
from datetime import timedelta

import requests
from django.conf import settings
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import F

from parishkit.config import ConfigError

from .policy_models import AddressMatch, PortalUser
from .policy_schema import normalized_email
from .users_schema import record_roles

LOGGER = logging.getLogger(__name__)

# The sensitive scope the second step asks for, and the People API read and
# token revocation it makes (#2352's live test, #2353's two-step tool).
EMAILS_SCOPE = "https://www.googleapis.com/auth/user.emails.read"
# ``people/me`` refuses a token without the basic profile scope (#2352,
# finding 2). Google reports a granted ``profile`` as its long form.
PROFILE_SCOPES = frozenset(
    {"profile", "https://www.googleapis.com/auth/userinfo.profile"}
)
# ``openid`` makes Google return an ID token for the second step, which binds
# it to the first step's account; ``profile`` is for people/me alone.
SECOND_STEP_SCOPES = ("openid", "profile", EMAILS_SCOPE)
PEOPLE_URL = "https://people.googleapis.com/v1/people/me?personFields=emailAddresses"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_TIMEOUT_SECONDS = 10
# How long a match stays usable after Google last confirmed it, and how long
# the second step stays open after the first callback.
CONFIRMATION_DAYS = 30
PENDING_SECONDS = 15 * 60
# The HTTP client of the two Google requests; tests put a fake Google here.
HTTP = requests
# The server-side pre-sign-in session key that holds the pending second step.
PENDING_KEY = "address_check"
# The users' ordering lock, which configuration activation also takes.
USERS_LOCK = (736220, 1)
# The runtime logins that read portal users but hold no grant on the match
# table: the streaming download and the background services. ``usable``
# trusts the matched address for these alone (the SQL checks they run trust
# it too); a permission error under any other login, the web's above all,
# refuses (#2374, Review A L2).
NO_MATCH_GRANT_LOGINS = ("download", "worker", "scheduler")


class GoogleUnavailable(Exception):
    """Google's People API could not answer; the check did not complete."""


def enabled():
    """Whether this web deployment's ``alternate_address_sign_in`` is on."""
    return getattr(settings, "STEWARDSHIP_ALTERNATE_ADDRESS_SIGN_IN", False) is True


def take_users_lock():
    """Take the users' ordering lock before locking any portal user row."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", list(USERS_LOCK))


def now():
    """The database's statement time, which the guards compare against."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        return cursor.fetchone()[0]


# ---------------------------------------------------------------------------
# What Google says


def granted(scope_text):
    """Whether the token response's ``scope`` holds what the check needs.

    Both the email-address scope and the basic profile scope (in either of
    Google's spellings) must be there; without either the person declined
    the permission, which Google shows as its own checkbox.
    """
    granted_scopes = set(scope_text.split()) if isinstance(scope_text, str) else set()
    return EMAILS_SCOPE in granted_scopes and bool(granted_scopes & PROFILE_SCOPES)


def verified_addresses(person):
    """The Google Account's own verified addresses in a People API answer.

    Only an entry whose ``metadata.verified`` is ``true`` and whose
    ``metadata.source.type`` is ``ACCOUNT`` counts; a profile, contact or
    domain-profile entry never does, even when flagged verified. Each is
    normalized as a sign-in address is; one that is not a valid address is
    skipped. The answer is a set, so a repeated address counts once.
    """
    found = set()
    entries = person.get("emailAddresses") if isinstance(person, dict) else None
    for entry in entries if isinstance(entries, list) else ():
        if not isinstance(entry, dict):
            continue
        metadata = entry.get("metadata")
        source = metadata.get("source") if isinstance(metadata, dict) else None
        if (
            not isinstance(metadata, dict)
            or metadata.get("verified") is not True
            or not isinstance(source, dict)
            or source.get("type") != "ACCOUNT"
        ):
            continue
        try:
            found.add(normalized_email(entry.get("value")))
        except ConfigError:
            continue
    return frozenset(found)


def _timed(what, call):
    """Run one bounded Google request, logging a timeout or error.

    The log names what was asked, the limit and the elapsed time, never a
    token or an address (the shared rule that every timeout is logged).
    Returns the response, or raises ``GoogleUnavailable``.
    """
    started = time.monotonic()
    try:
        return call()
    except requests.Timeout:
        LOGGER.warning(
            "Google %s timed out (limit %ss, elapsed %.1fs)",
            what,
            GOOGLE_TIMEOUT_SECONDS,
            time.monotonic() - started,
        )
    except requests.RequestException as error:
        LOGGER.warning(
            "Google %s failed (%s; limit %ss, elapsed %.1fs)",
            what,
            type(error).__name__,
            GOOGLE_TIMEOUT_SECONDS,
            time.monotonic() - started,
        )
    raise GoogleUnavailable(what)


def fetch_addresses(access_token, *, http=None):
    """Ask the People API once for the account's addresses; keep the verified.

    One ``people/me?personFields=emailAddresses`` call with a 10-second
    limit. A timeout, a connection failure, an error status or an answer
    that is not JSON raises ``GoogleUnavailable``; the answer itself is
    never logged or stored.
    """
    http = http or HTTP
    response = _timed(
        "People API request",
        lambda: http.get(
            PEOPLE_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=GOOGLE_TIMEOUT_SECONDS,
        ),
    )
    if response.status_code != 200:
        LOGGER.warning("Google People API answered HTTP %s", response.status_code)
        raise GoogleUnavailable("People API request")
    try:
        person = response.json()
    except ValueError:
        LOGGER.warning("Google People API answered something that is not JSON")
        raise GoogleUnavailable("People API request") from None
    return verified_addresses(person)


def revoke(access_token, *, http=None):
    """Revoke the second step's access token at Google, whatever happened.

    Revoking ends the whole grant, so no token outlives the request. A
    failure is a warning, never with the token, and changes nothing: the
    token expires within the hour anyway. Returns whether Google confirmed.
    """
    if not isinstance(access_token, str) or not access_token:
        return False
    http = http or HTTP
    try:
        response = _timed(
            "token revocation",
            lambda: http.post(
                REVOKE_URL,
                data={"token": access_token},
                timeout=GOOGLE_TIMEOUT_SECONDS,
            ),
        )
    except GoogleUnavailable:
        return False
    if response.status_code != 200:
        LOGGER.warning("Google token revocation answered HTTP %s", response.status_code)
        return False
    return True


# ---------------------------------------------------------------------------
# What the configuration says


@dataclass(frozen=True)
class User:
    """One user of the active configuration: its address and record ID."""

    email: str
    record_id: str


def users(store):
    """The active configuration's users, by address.

    A user is a ``login_rules`` record that grants a role, as the match
    guard's ``stewardship_address_rule`` read is; the configuration's
    coherence check has already verified the document against SQL.
    """
    from .configuration_installation import coherent_configuration

    document = coherent_configuration(store).active_configuration.canonical_document
    found = {}
    for record in document["sections"].get("login_rules", []):
        values = record["values"]
        if values.get("kind") in ("address", "user") and record_roles(values):
            found[values["email"]] = User(values["email"], str(record["id"]))
    return found


def is_user(records, match):
    """Whether ``records`` (login rules) still hold the match's user record."""
    return any(
        str(record["id"]) == str(match.user_record_id)
        and record["values"].get("email") == match.email
        and record["values"].get("kind") in ("address", "user")
        and record_roles(record["values"])
        for record in records
    )


def confirmed_recently(match, instant):
    """Whether Google confirmed ``match`` less than 30 days before ``instant``."""
    return match.confirmed_at > instant - timedelta(days=CONFIRMATION_DAYS)


# ---------------------------------------------------------------------------
# Every request


def usable(user, records):
    """Whether ``user``, which acts as another address, may still do so now.

    ``current_principal`` asks this only for an account whose ``email``
    differs from its ``sign_in_email``. The match must stand, name that
    ``email``, have been confirmed less than 30 days ago and still serve the
    user record it was made for, and the setting must be on. When it is not
    usable, the account's ``email`` goes back to its sign-in address
    (``put_back``), so the worker's SQL re-checks agree from the first
    refused request. A login without a grant on the match table (the
    download stream, a worker; ``NO_MATCH_GRANT_LOGINS``) cannot read it: it
    trusts the address, as the SQL checks it runs do, and the next web
    request decides. The trust is keyed on who the login is, never on the
    error alone, so a web login that lost its grant refuses instead.
    """
    try:
        with transaction.atomic():
            match = (
                AddressMatch.objects.filter(
                    portal_user_id=user.pk, ended_at__isnull=True, email=user.email
                )
                .only("id", "email", "user_record_id", "confirmed_at")
                .first()
            )
    except DatabaseError as error:
        if getattr(error.__cause__, "sqlstate", None) != "42501":
            raise
        if lacks_match_grant():
            return True
        LOGGER.warning(
            "This database login cannot read verified-address matches; "
            "the matched account gets no access"
        )
        match = None
    if (
        enabled()
        and match is not None
        and confirmed_recently(match, now())
        and is_user(records, match)
    ):
        return True
    put_back(user.pk)
    return False


def lacks_match_grant():
    """Whether this connection's login is one that has no match-table grant.

    Compares PostgreSQL's ``session_user`` with the provisioned names of
    ``NO_MATCH_GRANT_LOGINS``; the web login and any login not named there
    answer False.
    """
    from parishkit.stewardship.runtime_grants import login_name

    with connection.cursor() as cursor:
        cursor.execute("SELECT session_user")
        login = cursor.fetchone()[0]
    return login in {login_name(role) for role in NO_MATCH_GRANT_LOGINS}


def session_limit(user_id):
    """The latest end an automation session approved by ``user_id`` may have.

    None for an account that acts as its own sign-in address. An account
    that acts through a verified-address match may not approve a session
    that outlives the match's 30 days (admin-automation specification,
    "Session rules"): the limit is the match's ``confirmed_at`` plus 30
    days. An account that acts as another address without a standing match
    of it (which ``current_principal`` has already refused) gets the
    database's present time, so no session can be approved.
    """
    user = PortalUser.objects.only("email", "sign_in_email").get(pk=user_id)
    if user.email == user.sign_in_email:
        return None
    match = (
        AddressMatch.objects.filter(
            portal_user_id=user_id, ended_at__isnull=True, email=user.email
        )
        .only("confirmed_at")
        .first()
    )
    if match is None:
        return now()
    return match.confirmed_at + timedelta(days=CONFIRMATION_DAYS)


def put_back(user_id):
    """Make an account act as its own sign-in address again.

    The portal user guard always allows it, with no fresh verification. The
    users' ordering lock comes first, as for every match write. A login that
    may not write portal users, or a read-only transaction, leaves it for
    the next web request or sign-in.
    """
    try:
        with transaction.atomic():
            take_users_lock()
            PortalUser.objects.filter(pk=user_id).exclude(
                email=F("sign_in_email")
            ).update(email=F("sign_in_email"), version=F("version") + 1)
    except DatabaseError as error:
        if getattr(error.__cause__, "sqlstate", None) not in {"42501", "25006"}:
            raise


# ---------------------------------------------------------------------------
# The first callback


@dataclass(frozen=True)
class Refresh:
    """What the first callback's refresh found.

    ``match`` is the standing match the account now acts through (its sign-in
    records the match's ID), or None. ``offer`` is None, ``"first"`` (show
    the check page) or ``"again"`` (show its 30-day form for ``due``).
    """

    match: AddressMatch | None = None
    offer: str | None = None
    due: AddressMatch | None = None


def standing(user_id):
    """The standing match of one account, locked."""
    return (
        AddressMatch.objects.select_for_update()
        .filter(ended_at__isnull=True, portal_user_id=user_id)
        .first()
    )


def held_by_another(address, user_id):
    """Another account's standing match of ``address``, locked, or None."""
    return (
        AddressMatch.objects.select_for_update()
        .filter(ended_at__isnull=True, email=address)
        .exclude(portal_user_id=user_id)
        .first()
    )


def end(match, reason):
    """End one standing match, naming no actor (the guard re-derives why).

    The caller has already put the account's ``email`` back, as the guard
    requires. A plain update keeps ``actor_id`` NULL.
    """
    AddressMatch.objects.filter(pk=match.pk, ended_at__isnull=True).update(
        ended_at=now(), end_reason=reason, version=F("version") + 1
    )


def refresh(store, user, *, created, email, hosted):
    """The first callback's refresh of a portal user, with the setting on.

    The caller has taken the users' lock and locked the row. Writes the
    account's ``email`` as the data specification's table says, in one
    update with the fresh verification, then ends a match this sign-in ends:
    ``sign_in_address_matches`` when the sign-in address is a user's,
    ``user_gone`` when the match's user record is gone. Returns a
    ``Refresh``. A disabled account is refreshed as today and offered
    nothing.
    """
    configured = users(store)
    match = None if created else standing(user.pk)
    acts_as = email
    result = Refresh()
    ending = None
    if user.disabled:
        pass
    elif email in configured:
        ending = match and "sign_in_address_matches"
    elif match is None:
        result = Refresh(offer="first")
    elif configured.get(match.email) != User(match.email, str(match.user_record_id)):
        ending = "user_gone"
        result = Refresh(offer="first")
    elif confirmed_recently(match, now()):
        acts_as = match.email
        result = Refresh(match=match)
    else:
        result = Refresh(offer="again", due=match)
    if not created:
        PortalUser.objects.filter(pk=user.pk).update(
            email=acts_as,
            sign_in_email=email,
            hosted_domain=hosted,
            verified_at=now(),
            version=F("version") + 1,
        )
    if ending:
        end(match, ending)
    return result


# ---------------------------------------------------------------------------
# The second callback


@dataclass(frozen=True)
class Outcome:
    """A completed check's outcome.

    ``kind`` is ``"matched"`` (a new match), ``"rechecked"``, ``"sign_in"``
    (the sign-in address itself is now a user's) or one of the refusing
    outcomes (``declined``, ``none``, ``several``, ``own_account_only``,
    ``taken``). ``match`` is the match a successful sign-in acts through.
    """

    kind: str
    match: AddressMatch | None = None

    @property
    def signs_in(self):
        """Whether the account now signs in."""
        return self.kind in {"matched", "rechecked", "sign_in"}


def refresh_second(user, *, email, hosted):
    """Refresh the portal user from the second step's ID token.

    The account acts as its own sign-in address until the outcome says
    otherwise, so the guard sees a fresh verification inside the
    transaction that records the outcome.
    """
    PortalUser.objects.filter(pk=user.pk).update(
        email=email,
        sign_in_email=email,
        hosted_domain=hosted,
        verified_at=now(),
        version=F("version") + 1,
    )


def in_use(address, excluding):
    """``stewardship_address_in_use_v1``: the one answer the guard also uses."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_address_in_use_v1(%s, %s)", [address, excluding]
        )
        return cursor.fetchone()[0] is True


def act_as(user, address):
    """Make the account act as the matched ``address``, with a fresh stamp."""
    PortalUser.objects.filter(pk=user.pk).update(
        email=address, verified_at=now(), version=F("version") + 1
    )


def _guard_refusal(error):
    """The match guard's DETAIL on a refused insert, or None for another error."""
    cause = error.__cause__
    if getattr(cause, "sqlstate", None) != "23514":
        return None
    return getattr(getattr(cause, "diag", None), "message_detail", None)


def decide(store, user, *, email, addresses):
    """Decide a completed check and make its writes; returns an ``Outcome``.

    ``addresses`` are the account's verified addresses (None when the
    person declined). The caller has taken the users' lock, locked the
    account and refreshed it (``refresh_second``). The rules are the
    admin-portal specification's "Verified-address outcomes" and "Later
    sign-ins": the sign-in address wins; one user's address is a match or a
    re-check, unless it is a first Administrator address or its own Google
    account is in use (own account only), or another account's match of it
    still stands in use or confirmed (taken), in which case nothing is
    written but the account's own standing match ending; a lapsed match of
    another account that is not in use is replaced. A declined check leaves
    a standing match as it is.
    """
    configured = users(store)
    existing = standing(user.pk)
    if email in configured:
        if existing:
            end(existing, "sign_in_address_matches")
        return Outcome("sign_in")
    if addresses is None:
        return Outcome("declined")
    candidates = [
        configured[item] for item in sorted(addresses - {email}) if item in configured
    ]
    if len(candidates) != 1:
        return _refused(existing, "none" if not candidates else "several")
    chosen = candidates[0]
    if (
        existing
        and existing.email == chosen.email
        and str(existing.user_record_id) == chosen.record_id
        and existing.sign_in_email == email
    ):
        # Still verified and still the one match: Google confirmed it again.
        # SQL does not re-check that the account is enabled (the caller
        # refuses a disabled one), that its sign-in address is still the
        # match's, or that the address's own Google account is not in use,
        # so Python decides all three here. The last one stops a mail
        # delegate matched while the address's own account was dormant from
        # keeping the user once that account signs in (#2374).
        if in_use(chosen.email, user.pk):
            return _refused(existing, "own_account_only")
        AddressMatch.objects.filter(pk=existing.pk).update(
            confirmed_at=now(), version=F("version") + 1
        )
        act_as(user, chosen.email)
        return Outcome("rechecked", existing)
    other = held_by_another(chosen.email, user.pk)
    if in_use(chosen.email, other.portal_user_id if other else user.pk):
        # The address's own Google account signs in here.
        return _refused(existing, "own_account_only")
    if other and (confirmed_recently(other, now()) or in_use(chosen.email, user.pk)):
        return _refused(existing, "taken")
    # The guard decides every write below again. Sessions and sign-ins are
    # not under the users' lock, so the old account (or the address's own
    # account) may come into use after the Python checks above: the guard
    # then refuses the replaced end (Taken) or the insert (Own account
    # only). One savepoint undoes all of these writes, so a refusal leaves
    # nothing but the outcome's own end of the account's standing match.
    refusal = "taken"
    try:
        with transaction.atomic():
            if existing:
                # The account's earlier match no longer holds: Google
                # confirms another address now (one standing match each).
                end(existing, "not_confirmed")
            if other:
                # A lapsed match nobody uses: the newer account takes it
                # over, after the old account acts as its own address again.
                PortalUser.objects.select_for_update().filter(
                    pk=other.portal_user_id
                ).first()
                PortalUser.objects.filter(pk=other.portal_user_id).exclude(
                    email=F("sign_in_email")
                ).update(email=F("sign_in_email"), version=F("version") + 1)
                end(other, "replaced")
            refusal = "own_account_only"
            instant = now()
            made = AddressMatch.objects.create(
                portal_user_id=user.pk,
                email=chosen.email,
                user_record_id=chosen.record_id,
                sign_in_email=email,
                matched_at=instant,
                confirmed_at=instant,
            )
    except IntegrityError as error:
        if _guard_refusal(error) not in {
            "address_first_administrator",
            "address_own_account_in_use",
        }:
            raise
        return _refused(existing, refusal)
    act_as(user, chosen.email)
    return Outcome("matched", made)


def _refused(existing, kind):
    """A completed check that gives no access ends the account's standing match."""
    if existing:
        end(existing, "not_confirmed")
    return Outcome(kind)


# ---------------------------------------------------------------------------
# An Administrator's End match (Edit user and ``pk-admin users end-match``)


class OwnMatch(PermissionError):
    """The Administrator's own session signs in through this match."""


@dataclass(frozen=True)
class Ended:
    """What an Administrator's End match did.

    ``sign_in_email`` is the matched Google account's sign-in address as
    matched; ``already`` is True when the match had ended before this call,
    which then changed nothing.
    """

    sign_in_email: str
    already: bool


def standing_for(address, record_id):
    """The standing match of one user (address and record ID), or None.

    Edit user shows it and the command line lists it; a standing match
    made for an earlier record with the same address (the user deleted and
    added again) gives no access and ends at its account's next sign-in.
    """
    return (
        AddressMatch.objects.filter(
            email=address, user_record_id=record_id, ended_at__isnull=True
        )
        .only("id", "portal_user_id", "sign_in_email", "matched_at", "confirmed_at")
        .first()
    )


def end_by_administrator(match_id, address, *, actor_id, session_id):
    """End one user's match as an Administrator; returns an ``Ended``.

    ``address`` is the user's address: a match of another address is
    unknown (``LookupError``). The users' lock comes first, then the
    matched account's ``PortalUser`` row, then the match row, as every
    match write takes them (the notes on #2343). The match the
    Administrator's own session signs in through is refused (``OwnMatch``),
    as the own-Administrator guard refuses a self-demotion; a match that
    has already ended changes nothing. Otherwise the account acts as its
    sign-in address again and the match ends as ``ended_by_administrator``,
    naming the Administrator and their live session (``session_id``), by a
    plain update that leaves ``actor_id`` NULL. The match guard re-checks
    that session, records ``admin_address_match_ended`` and revokes the
    account's live Admin and automation sessions in the same statement.
    """
    with transaction.atomic():
        take_users_lock()
        found = (
            AddressMatch.objects.filter(pk=match_id, email=address)
            .values_list("portal_user_id", flat=True)
            .first()
        )
        if found is None:
            raise LookupError("The match is unavailable.")
        PortalUser.objects.select_for_update().filter(pk=found).first()
        match = AddressMatch.objects.select_for_update().get(pk=match_id)
        if match.ended_at is not None:
            return Ended(match.sign_in_email, already=True)
        if match.portal_user_id == actor_id:
            raise OwnMatch("You can't end the match you are signed in through.")
        PortalUser.objects.filter(pk=match.portal_user_id).exclude(
            email=F("sign_in_email")
        ).update(email=F("sign_in_email"), version=F("version") + 1)
        AddressMatch.objects.filter(pk=match.pk, ended_at__isnull=True).update(
            ended_at=now(),
            end_reason="ended_by_administrator",
            ended_by_id=actor_id,
            ended_session_id=session_id,
            version=F("version") + 1,
        )
        return Ended(match.sign_in_email, already=False)
