"""Production-mode Family sign-in through the SQL login function (#306 M3).

The validation deployment runs in Testing mode, so these tests are the only
place the Production branches of stewardship_family_login_v1 run before
launch: code MACs under a rotated two-key ring, links bound to the active
generation and the deployment's link epoch, and their refusals. Sign-ins go
through the real HTTP views and ``issue_family`` under the web grants;
refusals also call ``_mint_session`` directly, so SQL itself is shown to
refuse even where Python refuses first.
"""

from uuid import uuid4

import pytest
from django.db import transaction
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts.cryptography import CodeMacKeyring, Key
from parishkit.stewardship.accounts.family_authentication import _mint_session
from parishkit.stewardship.campaigns.credential_models import (
    FamilyAccessToken,
    FamilyCodeFingerprint,
    FamilySession,
)
from parishkit.stewardship.campaigns.link_tokens import rotate_token, token_context

from .auth_builders import unguarded
from .test_family_auth_postgresql import login
from .test_family_code_lookup_postgresql import production_codes  # noqa: F401
from .test_family_login_sql_postgresql import fresh_key, mint
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def production(production_codes, settings):  # noqa: F811
    """An activated Production campaign, its Family runtime and one live link."""
    campaign, ring, service, family, code = production_codes
    settings.STEWARDSHIP_FAMILY_RUNTIME = service
    row = FamilyAccessToken.objects.get(family=family)
    token = ring.private.decrypt(row.ciphertext, context=token_context(row.pk))
    return campaign, ring, family, code, row, token.decode()


def link(token):
    """Follow a personal link in a new browser; return the response status."""
    return Client(enforce_csrf_checks=True).get("/access/" + token).status_code


def test_production_code_and_link_sign_in_under_web_grants(production):
    """Both credentials mint Production sessions through the web login."""
    campaign, ring, family, code, _, token = production
    # Rotation left one lookup-only and one active key, so SQL re-matches
    # the code across both presented MACs.
    assert FamilyCodeFingerprint.objects.filter(family=family).count() == 2
    with web_login():
        client, response = login(code)
        assert response.status_code == 302
        assert client.get("/family/").status_code == 200
        assert link(token) == 302
    rows = list(FamilySession.objects.all())
    assert len(rows) == 2
    assert {row.family_id for row in rows} == {family.pk}
    assert {row.mode for row in rows} == {"production"}
    assert {row.rehearsal_epoch_id for row in rows} == {None}


def test_a_superseded_generation_link_is_refused(production):
    """A link outside the campaign's active generation mints nothing."""
    _, _, family, code, row, token = production
    with unguarded():
        row.generation.__class__.objects.filter(pk=row.generation_id).update(
            state="superseded", version=F("version") + 1
        )
    with web_login():
        assert link(token) == 403
        assert mint(family=family.pk, token=token) is None
        # A code does not depend on the link generation.
        assert login(code)[1].status_code == 302
    assert FamilySession.objects.count() == 1


def test_a_rotated_link_is_refused_and_its_replacement_admitted(production):
    """Rotation revokes the previous link value in SQL as well as Python."""
    _, ring, family, _, row, token = production
    rotate_token(token_id=row.pk, public=ring.public, admit=lambda *args: True)
    row.refresh_from_db()
    replacement = ring.private.decrypt(
        row.ciphertext, context=token_context(row.pk)
    ).decode()
    with web_login():
        assert link(token) == 403
        assert mint(family=family.pk, token=token) is None
        assert link(replacement) == 302
    assert FamilySession.objects.count() == 1


def test_a_wrong_epoch_or_destroyed_link_is_refused(production):
    """A link whose generation is not on the deployment's epoch, or which was
    destroyed, is refused by SQL even when presented directly."""
    _, _, family, _, row, token = production
    with unguarded():
        row.generation.__class__.objects.filter(pk=row.generation_id).update(
            credential_epoch=uuid4(), version=F("version") + 1
        )
    with web_login():
        assert link(token) == 403
        assert mint(family=family.pk, token=token) is None
    with unguarded():
        row.generation.__class__.objects.filter(pk=row.generation_id).update(
            credential_epoch=row.generation.credential_epoch,
            version=F("version") + 1,
        )
        FamilyAccessToken.objects.filter(pk=row.pk).update(
            ciphertext=None,
            digest=None,
            destroyed_at=row.created_at,
            version=F("version") + 1,
        )
    with web_login():
        assert mint(family=family.pk, token=token) is None
    assert not FamilySession.objects.exists()


def test_a_full_ring_of_code_macs_is_accepted(production):
    """Up to 32 presented MACs (the largest ring) are accepted; more are not."""
    campaign, ring, family, code, _, _ = production
    real = ring.mac.lookups(campaign.pk, code)
    padding = {f"x{index}": f"{index:064x}" for index in range(32 - len(real))}
    with web_login():
        assert mint(family=family.pk, digests=real | padding) is not None
        assert mint(family=family.pk, digests=real | padding | {"y": "0" * 64}) is None
    # A 20-key ring's lookups, passed straight to _mint_session: Python
    # presents a MAC for every lookup key, and unknown keys match nothing.
    wide = CodeMacKeyring(
        [Key(key.id, key.usage, key.material) for key in ring.mac.keys.values()]
        + [Key(f"w{index}", "lookup-only", bytes([index]) * 32) for index in range(18)]
    )
    assert len(wide.lookups(campaign.pk, code)) == 20
    with web_login(), transaction.atomic():
        assert _mint_session(
            fresh_key(), family.pk, digests=wide.lookups(campaign.pk, code)
        )
