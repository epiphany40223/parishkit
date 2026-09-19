"""Private directory filters and bounded, coherent source/credential reads."""

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import connection
from django.db.models import Q
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts.cryptography import canonical_code
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    FamilyCodeFingerprint,
)
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import filters

from .directory_query import DIRECTORY
from .information import parse_page

PAGE_SIZE = 50
REASONS = {
    "no_head": "No active Family head",
    "no_address": "No head email address",
    "invalid_address": "No valid head email address",
    "provider_refused": "All eligible addresses permanently refused",
    "deliverable": "Deliverable email available",
}


@dataclass(frozen=True, repr=False)
class DirectoryQuery:
    """Identifying values travel only in CSRF POST bodies, never links or logs."""

    search: str = ""
    exact_code: str = ""
    reason: str = "any"
    phone: str = "any"
    response: str = "any"
    sort: str = "name"
    page: int = 1

    @classmethod
    def parse(cls, parameters):
        """Reject duplicate/unknown values and canonicalize friendly manual codes."""
        if type(parameters) is dict:
            if any(type(value) is not str for value in parameters.values()):
                raise ValueError("Directory filters require text values.")
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        values = filters(parameters, allowed=set(cls.__dataclass_fields__))
        values["page"] = parse_page(values.get("page", "1"))
        query = cls(**values)
        bounded_text(query.search)
        if (
            len(query.search) > 200
            or query.reason not in {"any", *REASONS}
            or query.phone not in {"any", "yes", "no"}
            or query.response not in {"any", "yes", "no"}
            or query.sort not in {"name", "name_desc", "duid"}
        ):
            raise ValueError("Invalid directory filters.")
        if query.exact_code:
            code = canonical_code(query.exact_code)
            if code is None:
                raise ValueError("Invalid exact-code filter.")
            values["exact_code"] = code
        return cls(**values)

    def form_values(self):
        """Templates escape private POST state for filters and page navigation."""
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key != "page"
        }

    def audit_values(self):
        """Only closed filter dimensions/presence flags may enter durable audit."""
        return {
            "directory_reason": self.reason,
            "directory_phone": self.phone,
            "directory_response": self.response,
            "directory_sort": self.sort,
            "page": self.page,
            "search_used": bool(self.search),
            "exact_code_used": bool(self.exact_code),
        }


def address_lines(address):
    """Render known nonblank components without displaying Python null values."""

    def value(key):
        """A missing and a known-blank component both contribute no printed text."""
        return str(address.get(key) or "").strip()

    lines = [value(f"primaryAddress{index}") for index in (1, 2, 3)]
    postal = value("primaryPostalCode")
    plus = value("primaryZipPlus")
    lines.append(
        " ".join(
            filter(
                None,
                (
                    value("primaryCity"),
                    value("primaryState"),
                    "-".join(filter(None, (postal, plus))),
                ),
            )
        )
    )
    return tuple(filter(None, lines))


def selection_parameters(campaign_id, query, *, postal, mac):
    """Bind private exact-code input to an immutable Family ID under key locks.

    Persisting this selection never persists a plaintext code or a MAC key
    version. Regeneration therefore remains stable after ordinary key rotation.
    Unknown codes retain an explicit empty exact selection, not an unfiltered
    query. Callers hold the campaign read/work barrier and key inventory lock.
    """
    if not isinstance(campaign_id, UUID) or not isinstance(query, DirectoryQuery):
        raise ValueError("Directory selection requires typed scope and filters.")
    if type(postal) is not bool:
        raise ValueError("Directory kind must be explicit.")
    query = DirectoryQuery.parse(query.form_values() | {"page": str(query.page)})
    family_id = None
    if query.exact_code:
        candidates = mac.lookups(campaign_id, query.exact_code)
        choices = Q(pk__in=[])
        for key, digest in candidates.items():
            choices |= Q(key_id=key, digest=digest)
        identities = list(
            FamilyCodeFingerprint.objects.filter(choices, campaign_id=campaign_id)
            .values_list("family_id", flat=True)
            .distinct()[:2]
        )
        if len(identities) > 1:
            raise ReadUnavailable("Exact-code selection is unavailable.")
        family_id = str(identities[0]) if identities else None
    values = query.form_values()
    values.pop("exact_code")
    return {
        "filters": values,
        "postal": postal,
        "exact": bool(query.exact_code),
        "family_id": family_id,
    }


def add_codes(campaign_id, rows, *, general):
    """Decrypt only the selected identities under the caller's key/read barriers."""
    identities = {
        str(row.pk): row
        for row in FamilyCampaign.objects.filter(
            campaign_id=campaign_id,
            pk__in=[row["family_id"] for row in rows if row["family_id"]],
        ).only("id", "code_ciphertext")
    }
    for row in rows:
        identity = identities.get(row["family_id"])
        row["code"] = (
            general.decrypt(
                identity.code_ciphertext, context=code_context(identity.pk)
            ).decode("ascii")
            if identity and identity.code_ciphertext
            else None
        )
        row["reason_label"] = REASONS[row["reason"]]
        row["address_lines"] = address_lines(row["address"])


def directory_page(campaign_id, query, *, postal, general, mac):
    """Read one SQL page, then decrypt only its codes under the caller's read guard.

    Campaign identity/code values never change during retained campaign life.
    The key-inventory lock prevents rotation between selection and decryption;
    the outer campaign read guard prevents purge through response completion.
    No Family session, public limiter or opaque email token is involved.
    """
    with key_set_lock(general, mac):
        parameters = selection_parameters(campaign_id, query, postal=postal, mac=mac)
        with connection.cursor() as cursor:
            cursor.execute(
                DIRECTORY,
                (campaign_id, json.dumps(parameters), query.page),
            )
            result = cursor.fetchone()
        if result is None or result[0] is None:
            raise ReadUnavailable("Directory source information is unavailable.")
        report = json.loads(result[0])
        add_codes(campaign_id, report["rows"], general=general)
        report["metadata"]["source_as_of"] = datetime.fromisoformat(
            report["metadata"]["source_as_of"]
        )
        return report
