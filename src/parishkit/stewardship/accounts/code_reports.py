"""Authorized stable Production-code access, independent of public guessing controls.

The main navigation uses the source-backed RPT-05 directory. This bounded
foundation remains a recovery endpoint when no usable source snapshot exists:
it lists retained campaign identities only, never claims source contact details.
"""

from django.db import DatabaseError, transaction
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
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
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.tables import Sorting, bounded_count, window_table

from .authentication import denial, runtime
from .cryptography import CryptographicError
from .family_authentication import runtime as family_runtime
from .limiting import LimiterUnavailable
from .models import SystemConfiguration
from .policy import Capability, allows
from .sessions import authenticated_admin

# Family DUID sorts on the server through the family_campaign_duid unique
# index (campaign_id, family_duid), which also makes it its own tiebreak.
# The Code column is deliberately not sortable: codes are stored encrypted,
# and sorting by them would mean decrypting every Family's code on every
# page view; a random code's order means nothing to a reader anyway.
CODE_SORTING = Sorting.by_column(
    {"duid": ("family_duid",)}, default="duid", tiebreak=("id",)
)


@require_safe
def family_codes(request, campaign_id):
    """Audit intent before a read-only response; recheck roles under its read guard."""
    finish, handed_off = None, False
    try:
        service, cryptographic = runtime(), family_runtime()
        principal = authenticated_admin(request, store=service.store, activity=True)
        if not allows(principal, Capability.FAMILY_CODES):
            return denial()
        try:
            parsed = filters(request.GET, allowed={"page", "size", "sort"})
            sort = CODE_SORTING.parse(parsed)
            window = PageWindow(
                expected_version(parsed.get("page", "1")),
                expected_version(parsed.get("size", "50")),
            )
        except ValueError:
            response = render(
                request,
                "stewardship/invalid_report.html",
                {"retry_path": reverse("admin:family_codes", args=[campaign_id])},
                status=400,
            )
            response.stewardship_safe_error = True
            return response
        configuration = SystemConfiguration.objects.get()
        if configuration.restore_review_required:
            return report_unavailable()
        with transaction.atomic():
            record_action(
                Action.FAMILY_CODES_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=principal.identity,
                parish_id=configuration.active_configuration.parish.pk,
                campaign_id=campaign_id,
                context={"outcome": Outcome.STARTED},
            )
        prepared_count, finalized = 0, False

        def finish(completed):
            """Audit server completion only after the response read guard is closed."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            with transaction.atomic():
                record_action(
                    Action.FAMILY_CODES_VIEWED,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=principal.identity,
                    parish_id=configuration.active_configuration.parish.pk,
                    campaign_id=campaign_id,
                    context={
                        "outcome": Outcome.SUCCEEDED if completed else Outcome.FAILED,
                        "count": prepared_count,
                    },
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
            nonlocal prepared_count
            with key_set_lock(cryptographic.general):
                families = FamilyCampaign.objects.filter(
                    campaign_id=campaign_id,
                    active=True,
                    code_ciphertext__isnull=False,
                )
                rows, has_next = window.rows(CODE_SORTING.order(families, sort))
                table = [
                    {
                        "duid": row.family_duid,
                        "code": cryptographic.general.decrypt(
                            row.code_ciphertext,
                            context=code_context(row.pk),
                        ).decode("ascii"),
                    }
                    for row in rows
                ]
                body = render_to_string(
                    "stewardship/codes.html",
                    {
                        "table": window_table(
                            window,
                            table,
                            has_next,
                            total=bounded_count(families),
                            sorting=CODE_SORTING,
                            sort=sort,
                        ),
                    },
                ).encode()
                prepared_count = len(table)
                return iter((body,))

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
        )
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (
        ConfigError,
        CryptographicError,
        LimiterUnavailable,
        UnicodeError,
        DatabaseError,
        TypeError,
        ValueError,
    ):
        return report_unavailable()
    finally:
        # Once returned, the stream owns terminal audit; all earlier exits,
        # including unexpected serializer exceptions, finish here instead.
        if finish is not None and not handed_off:
            finish(False)
