"""Authorized stable Production-code access, independent of public guessing controls.

Names/full-source filters are added by the report owner when DAT-03 is present.
This bounded foundation lists existing campaign identities, never a shadow source.
"""

from django.db import transaction
from django.template.loader import render_to_string
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.web.contracts import (
    PageWindow,
    expected_version,
    filters,
)
from parishkit.stewardship.web.responses import campaign_response

from .authentication import denial, runtime
from .cryptography import CryptographicError
from .family_authentication import runtime as family_runtime
from .limiting import LimiterUnavailable
from .models import SystemConfiguration
from .policy import Capability, allows
from .sessions import authenticated_admin


@require_safe
def family_codes(request, campaign_id):
    """Audit intent before a read-only response; recheck roles under its read guard."""
    try:
        service, cryptographic = runtime(), family_runtime()
        principal = authenticated_admin(request, store=service.store, activity=True)
        if not allows(principal, Capability.FAMILY_CODES):
            return denial()
        parsed = filters(request.GET, allowed={"page", "size"})
        window = PageWindow(
            expected_version(parsed.get("page", "1")),
            expected_version(parsed.get("size", "50")),
        )
        configuration = SystemConfiguration.objects.get()
        if configuration.restore_review_required:
            return denial(status=503, retry=5)
        with transaction.atomic():
            record_action(
                Action.FAMILY_CODES_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=principal.identity,
                parish_id=configuration.active_configuration.parish.pk,
                campaign_id=campaign_id,
                context={"outcome": Outcome.STARTED},
            )

        def authorize(guard):
            """A stale role-bearing cookie is not permission to decrypt or stream."""
            current = authenticated_admin(request, store=service.store, read_only=True)
            if not allows(current, Capability.FAMILY_CODES):
                raise ReadUnavailable("This report is unavailable.")

        def content():
            """Prepare the bounded page under the guard before HTTP headers commit.

            This must be an ordinary function, not a generator: key contention
            and decryption failures must reach the view's retryable error path.
            """
            with key_set_lock(cryptographic.general):
                rows, has_next = window.rows(
                    FamilyCampaign.objects.filter(
                        campaign_id=campaign_id,
                        active=True,
                        code_ciphertext__isnull=False,
                    ).order_by("family_duid")
                )
                table = [
                    [
                        str(row.family_duid),
                        cryptographic.general.decrypt(
                            row.code_ciphertext,
                            context=code_context(row.pk),
                        ).decode("ascii"),
                    ]
                    for row in rows
                ]
                body = render_to_string(
                    "stewardship/codes.html",
                    {
                        "table_rows": table,
                        "table_caption": _("Active Families"),
                        "table_headings": [_("Family DUID"), _("Code")],
                        "has_next": has_next,
                        "page": window.page,
                        "size": window.size,
                        "next_page": window.page + 1,
                        "previous_page": window.page - 1,
                    },
                ).encode()
                return iter((body,))

        return campaign_response(
            request, [campaign_id], authorize=authorize, open_content=content
        )
    except (ConfigError, CryptographicError, LimiterUnavailable):
        return denial(status=503, retry=5)
    except ValueError:
        return denial(status=400)
