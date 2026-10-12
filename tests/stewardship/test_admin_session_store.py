"""The Admin session store's digest and its namespace selection (#995).

Database behavior (sign-in, replay, the SQL guard and migration 0057) is in
``database/test_admin_session_key_digests_postgresql.py``.
"""

import asyncio
import hashlib
from importlib import import_module

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory

from parishkit.stewardship.accounts.admin_session_store import (
    SessionStore,
    digest,
    stored_key,
)
from parishkit.stewardship.accounts.sessions import NamespacedSessionMiddleware


def test_digest_is_the_first_forty_hex_digits_of_sha256():
    """Forty digits fit Django's session_key and never equal a 32-character key."""
    key = "abcdefghijklmnopqrstuvwxyz012345"
    assert digest(key) == hashlib.sha256(key.encode()).hexdigest()[:40]
    assert digest(key) == "653bb1245e828fcda4fa53fcd5a3def5bd7654e6"
    assert len(digest(key)) == 40 != len(key)


def test_stored_key_is_only_an_admin_stores_digest():
    """A Family store, or none, names no Admin session row."""
    admin = SessionStore("abcdefghijklmnopqrstuvwxyz012345")
    assert stored_key(admin) == admin.stored_key == digest(admin.session_key)
    assert SessionStore().stored_key is None
    family = import_module(settings.SESSION_ENGINE).SessionStore(
        "abcdefghijklmnopqrstuvwxyz012345"
    )
    assert stored_key(family) is None
    assert stored_key(None) is None


@pytest.mark.parametrize(
    "call",
    [
        lambda store: store.aexists("abcdefghijklmnopqrstuvwxyz012345"),
        lambda store: store.adelete(),
        lambda store: store.acreate_model_instance({}),
        lambda store: store._aget_session_from_db(),
    ],
)
def test_async_methods_are_refused(call):
    """Django's inherited async methods would store an undigested key."""
    with pytest.raises(NotImplementedError):
        asyncio.run(call(SessionStore("abcdefghijklmnopqrstuvwxyz012345")))


@pytest.mark.parametrize(
    "path, admin", [("/admin/", True), ("/admin/setup", True), ("/", False)]
)
def test_middleware_picks_the_admin_store_only_for_admin_paths(path, admin):
    """Family requests keep Django's store, so Family sessions are unchanged."""
    seen = []

    def view(request):
        seen.append(type(request.session))
        return HttpResponse()

    NamespacedSessionMiddleware(view)(RequestFactory().get(path))
    assert (seen[0] is SessionStore) is admin
    if not admin:
        assert seen[0] is import_module(settings.SESSION_ENGINE).SessionStore


def test_a_family_cookie_in_digest_form_opens_no_session():
    """An Admin digest presented as pk_family never loads that Admin row."""
    seen = []

    def view(request):
        seen.append(request.session.session_key)
        return HttpResponse()

    request = RequestFactory().get("/")
    request.COOKIES["pk_family"] = digest("abcdefghijklmnopqrstuvwxyz012345")
    NamespacedSessionMiddleware(view)(request)
    assert seen == [None]
