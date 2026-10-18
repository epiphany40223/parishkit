"""A Family's contact details for its Additional information item (#1595).

Staff follow up what a Family wrote, so the item page shows the Family's
operational email addresses and phones: the Family record's, then each
active head of household's, the heads the page's Family name lists
(``snapshot_names``). One statement reads them from the snapshot the page
read the item from, the way the directory reads Family head emails, so no
schema change was needed. Like that read, it sees the snapshot whole or not
at all: compaction marks a snapshot compacted before deleting any of its
rows, so a statement that sees it uncompacted sees every row.

The values are operational contact (the reports spec's Ministry change
summary): Administrators and Staff see them whatever ParishSoft's publish
flags say. Only the item page, which only they may open, reads them; the
read checks the capability again, so no other caller can reach the values
without it.
"""

import json

from django.db import connection
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.accounts.policy import Capability, allows

from .directories import INVALID_EMAIL_NOTE

# The snapshot's state, the Family row, its active heads' Member rows and
# every contact row of the Family and those heads, in one statement (see the
# module docstring). A head list that is not an array names no heads.
_CONTACTS_SQL = """
WITH snapshot AS (
    SELECT id FROM stewardship_source_snapshot
    WHERE id=%(snapshot)s AND state='promoted' AND compacted_at IS NULL
), family AS (
    SELECT p.canonical::jsonb AS value
    FROM stewardship_snapshot_family f
    JOIN stewardship_source_family p ON p.id=f.payload_id
    WHERE f.snapshot_id=(SELECT id FROM snapshot) AND f.source_key=%(family)s
), heads AS (
    SELECT m.source_key, p.canonical::jsonb AS value
    FROM stewardship_snapshot_member m
    JOIN stewardship_source_member p ON p.id=m.payload_id
    WHERE m.snapshot_id=(SELECT id FROM snapshot)
      AND m.source_key IN (
        SELECT jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(value->'active_head_duids')='array'
            THEN value->'active_head_duids' ELSE '[]'::jsonb END)
        FROM family)
), contacts AS (
    SELECT c.source_key, p.canonical::jsonb AS value
    FROM stewardship_snapshot_contact c
    JOIN stewardship_source_contact p ON p.id=c.payload_id
    WHERE c.snapshot_id=(SELECT id FROM snapshot)
      AND c.source_key IN (
        SELECT 'family:'||%(family)s
        UNION ALL SELECT 'member:'||source_key FROM heads)
)
SELECT EXISTS (SELECT 1 FROM snapshot), EXISTS (SELECT 1 FROM family),
    (SELECT jsonb_object_agg(source_key, value) FROM heads)::text,
    (SELECT jsonb_object_agg(source_key, value) FROM contacts)::text
"""

# Each ParishSoft phone the source keeps, as the page names it, in the
# order shown: a Family record has home and primary, a Member home, mobile
# and work.
PHONE_KINDS = {
    "primary": _("Primary"),
    "home": _("Home"),
    "mobile": _("Mobile"),
    "work": _("Work"),
}


def _details(label, contact):
    """One owner's emails and phones, as the page lists them.

    ``emails`` are display texts, an invalid source address marked as the
    directory marks it; ``phones`` are ``(label, value)`` pairs in
    ``PHONE_KINDS`` order. A missing contact record has neither.
    """
    contact = contact or {}
    emails = [
        email["value"]
        if email.get("valid")
        else f"{email['value']} ({INVALID_EMAIL_NOTE})"
        for email in contact.get("emails") or ()
        if email.get("value")
    ]
    phones = contact.get("phones") or {}
    return {
        "label": label,
        "emails": emails,
        "phones": [
            (name, phones[kind])
            for kind, name in PHONE_KINDS.items()
            if phones.get(kind)
        ],
    }


def family_contacts(principal, snapshot_id, family_duid):
    """The item page's Contact details for one Family, read from ``snapshot_id``.

    Returns ``{"state": "shown", "owners": [...]}``, the Family record first
    and then each active head in DUID order (each from ``_details``);
    ``{"state": "missing"}`` when the snapshot has no such Family; or
    ``{"state": "unavailable"}`` when the snapshot was compacted or replaced
    before this read (the page then asks for a reload). Raises
    ``PermissionError`` for a reader without ``ADDITIONAL_FOLLOWUP``, before
    anything is read.
    """
    if not allows(principal, Capability.ADDITIONAL_FOLLOWUP):
        raise PermissionError("Family contact details are unavailable.")
    with connection.cursor() as cursor:
        cursor.execute(
            _CONTACTS_SQL, {"snapshot": str(snapshot_id), "family": str(family_duid)}
        )
        usable, present, head_text, contact_text = cursor.fetchone()
    if not usable:
        return {"state": "unavailable"}
    if not present:
        return {"state": "missing"}
    heads = json.loads(head_text or "{}")
    contacts = json.loads(contact_text or "{}")
    owners = [_details(_("Family record"), contacts.get(f"family:{family_duid}"))]
    for key in sorted(heads, key=int):
        member = heads[key]
        # Only active heads name the Family, so only they are listed.
        if member.get("active") is not True:
            continue
        name = " ".join(
            part
            for part in (
                (member.get("firstName") or "").strip(),
                (member.get("lastName") or "").strip(),
            )
            if part
        )
        owners.append(
            _details(name or _("Head of household"), contacts.get(f"member:{key}"))
        )
    return {"state": "shown", "owners": owners}
