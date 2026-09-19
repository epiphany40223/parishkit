"""Private directory filters and bounded, coherent source/credential reads."""

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import connection

from parishkit.stewardship.accounts.cryptography import canonical_code
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
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


def directory_page(campaign_id, query, *, postal, general, mac):
    """Read one SQL page, then decrypt only its codes under the caller's read guard.

    Campaign identity/code values never change during retained campaign life.
    The key-inventory lock prevents rotation between selection and decryption;
    the outer campaign read guard prevents purge through response completion.
    No Family session, public limiter or opaque email token is involved.
    """
    if not isinstance(campaign_id, UUID) or not isinstance(query, DirectoryQuery):
        raise ValueError("Directory selection requires typed scope and filters.")
    if type(postal) is not bool:
        raise ValueError("Directory kind must be explicit.")
    with key_set_lock(general, mac):
        candidates = (
            mac.lookups(campaign_id, query.exact_code) if query.exact_code else {}
        )
        with connection.cursor() as cursor:
            cursor.execute(
                DIRECTORY,
                {
                    "campaign": campaign_id,
                    "filters": json.dumps(query.form_values() | {"exact_code": ""}),
                    "exact": bool(query.exact_code),
                    "candidates": json.dumps(candidates),
                    "postal": postal,
                    "size": PAGE_SIZE,
                    "offset": (query.page - 1) * PAGE_SIZE,
                },
            )
            result = cursor.fetchone()
        if result is None or result[0] is None:
            raise ReadUnavailable("Directory source information is unavailable.")
        report = json.loads(result[0])
        if report.pop("code_matches") > 1:
            raise ReadUnavailable("Exact-code selection is unavailable.")
        identities = {
            str(row.pk): row
            for row in FamilyCampaign.objects.filter(
                campaign_id=campaign_id,
                pk__in=[row["family_id"] for row in report["rows"] if row["family_id"]],
            ).only("id", "code_ciphertext")
        }
        for row in report["rows"]:
            identity = identities.get(row["family_id"])
            row["code"] = (
                general.decrypt(
                    identity.code_ciphertext, context=code_context(identity.pk)
                ).decode("ascii")
                if identity and identity.code_ciphertext
                else None
            )
            row["reason_label"] = REASONS[row["reason"]]
        report["metadata"]["source_as_of"] = datetime.fromisoformat(
            report["metadata"]["source_as_of"]
        )
        return report
