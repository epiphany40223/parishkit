"""Admin session storage that keeps only a digest of each session key (#995).

The browser's ``pk_admin`` cookie carries a Django session key. Django's
database store would keep that same value in ``django_session.session_key``,
and ``stewardship_portal_session.session_id`` names it, so anyone who can
read those tables (the web database login, a backup) could replay a live
Administrator's key as a cookie or as the session argument of a definer.

This store keeps the database row under ``digest(key)`` instead: the first
40 hexadecimal digits of the key's SHA-256. The cookie still carries the
key, and every lookup digests the presented key first, so a stored value
is useless as a cookie. Django keys are 32 random characters from a
36-letter alphabet (about 165 bits), so an unkeyed digest cannot be
reversed by search, and SQL can compute the same value with
``public.stewardship_admin_session_key_v1(text)`` without holding a secret.
Forty digits fit Django's ``session_key`` and the ``PortalSession`` column
unchanged and never equal an undigested 32-character key, so an
undigested key that is stored by mistake can never be found by a lookup;
the ``stewardship_portal_session`` insert trigger refuses one outright.

Only the Admin namespace uses this store (``sessions.NamespacedSessionMiddleware``
picks it for ``/admin/``). Family sessions keep Django's database store and
their stored keys unchanged. Django's async session methods are refused
rather than inherited, because the inherited ones would store an undigested
key; nothing here uses them.
"""

import hashlib
import logging
import re

from django.contrib.sessions.backends import db
from django.core.exceptions import SuspiciousOperation
from django.utils import timezone

# The digest's length: Django's session_key column and PortalSession's
# session_id are varchar(40).
DIGEST_LENGTH = 40
# The stored form; Django's own keys are 32 characters, so never this.
STORED_FORM = re.compile(rf"[0-9a-f]{{{DIGEST_LENGTH}}}")


def digest(session_key):
    """The stored form of one Admin session key (SQL computes the same)."""
    return hashlib.sha256(session_key.encode()).hexdigest()[:DIGEST_LENGTH]


class SessionStore(db.SessionStore):
    """Django's database store, keyed by the digest of the cookie's key."""

    def _db_key(self, session_key):
        """The ``django_session`` key for a cookie key (None stays None)."""
        return None if session_key is None else digest(session_key)

    @property
    def stored_key(self):
        """This session's key as the database stores it, or None if unsaved."""
        return self._db_key(self.session_key)

    def _get_session_from_db(self):
        """Django's lookup, by the digest of the presented key."""
        try:
            return self.model.objects.get(
                session_key=self.stored_key, expire_date__gt=timezone.now()
            )
        except (self.model.DoesNotExist, SuspiciousOperation) as error:
            if isinstance(error, SuspiciousOperation):
                logging.getLogger(
                    f"django.security.{error.__class__.__name__}"
                ).warning(str(error))
            self._session_key = None

    def exists(self, session_key):
        """Whether a new key's digest is taken (Django's uniqueness probe)."""
        return self.model.objects.filter(session_key=self._db_key(session_key)).exists()

    def create_model_instance(self, data):
        """The row to save: Django's, under the key's digest."""
        instance = super().create_model_instance(data)
        instance.session_key = self._db_key(instance.session_key)
        return instance

    def delete(self, session_key=None):
        """Delete by cookie key, as Django's store does, through its digest."""
        if session_key is None:
            session_key = self.session_key
        if session_key is not None:
            self.model.objects.filter(session_key=self._db_key(session_key)).delete()

    async def _aget_session_from_db(self):
        raise NotImplementedError("Admin sessions are synchronous.")

    async def aexists(self, session_key):
        raise NotImplementedError("Admin sessions are synchronous.")

    async def acreate_model_instance(self, data):
        raise NotImplementedError("Admin sessions are synchronous.")

    async def adelete(self, session_key=None):
        raise NotImplementedError("Admin sessions are synchronous.")


def stored_key(session):
    """The stored key of an Admin session store, or None for any other store.

    Every ``PortalSession`` lookup by session goes through this, so a Family
    store, or no store, finds no Admin session rather than matching by an
    undigested key.
    """
    return session.stored_key if isinstance(session, SessionStore) else None
