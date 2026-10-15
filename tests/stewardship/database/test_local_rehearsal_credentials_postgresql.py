"""The full-size rehearsal's credential fingerprint against the real schema.

``rehearsal.CREDENTIALS_SQL`` (BG-12 PR 4, #447) is read before and after a
local send, and the two answers must match. These tests run it against the
migrated schema, so a renamed or dropped column fails here rather than in a
live rehearsal, and show that a real link rotation moves the fingerprint.
"""

import pytest
from django.db import connection, transaction

from parishkit.stewardship.campaigns.credential_models import FamilyAccessToken
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.link_tokens import rotate_token
from parishkit.stewardship.local.rehearsal import CREDENTIALS_SQL

from .campaign_builders import campaign_clock, command
from .credential_builders import family_campaign

pytestmark = pytest.mark.django_db(transaction=True)

KINDS = [
    "family_codes",
    "code_macs",
    "link_tokens",
    "token_generations",
    "link_fence",
    "campaign_generations",
    "rehearsal_epochs",
    "rehearsal_credentials",
]


@pytest.fixture
def active(tmp_path):
    """An activated campaign with one Family, its code and its link token."""
    store, campaign, actor, ring = family_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        campaign.refresh_from_db()
        yield campaign, ring


def fingerprint():
    """The fingerprint rows, read as the operator script reads them."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(CREDENTIALS_SQL)
        return {kind: (count, digest) for kind, count, digest in cursor.fetchall()}


def test_the_fingerprint_reads_every_kind_read_only(active):
    """One row per kind, real rows counted, and the same answer twice."""
    rows = fingerprint()
    assert list(rows) == KINDS
    assert rows["link_tokens"][0] == 1 and rows["family_codes"][0] >= 1
    assert rows["campaign_generations"][0] >= 1 and rows["link_fence"][0] == 1
    assert all(len(digest) in (1, 32) for _, digest in rows.values())
    assert fingerprint() == rows


def test_a_link_rotation_moves_only_the_link_tokens(active):
    """Rotating one Family's link (new ciphertext and rotated_at) is seen."""
    _, ring = active
    before = fingerprint()
    row = FamilyAccessToken.objects.get()
    rotate_token(token_id=row.pk, public=ring.public, admit=lambda *args: True)
    after = fingerprint()
    assert after["link_tokens"] != before["link_tokens"]
    assert {k: v for k, v in after.items() if k != "link_tokens"} == {
        k: v for k, v in before.items() if k != "link_tokens"
    }
