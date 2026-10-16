"""Whether a census change's conflict was resolved at ParishSoft's current value.

An Administrator's "Use the Family's value anyway" (#528, ADM-09.03) is a
``ProposalReview`` row that accepts ParishSoft's value at that moment as the
base publication merges against, with the snapshot the value came from
(``accepted_source_id``). The base is *active* while that snapshot is still
the proposal's ``current_source_id`` and no later decision review (one
recording a decision other than approved: Back to To review or Ignore) has
undone it; a later Edit value keeps the decision, so it keeps the base. Such
an undo also puts the change back to Conflict (``census_review._record``).
Reconciliation changes that reference
whenever, and only when, ParishSoft's value changes (canonically unchanged
values keep their reference), so an active base always equals the
proposal's ``current_value`` and goes stale by itself once ParishSoft moves.
The database holds every writer to the same rule
(``schema/migrations/0108_conflict_resolution.sql``).

This module imports only the models, so the planner, reconciliation and
response derivation can all use it without import cycles.
"""

from django.db.models import Exists, OuterRef

from .models import ProposalReview


def _undone():
    """Later decision reviews of the same proposal, for one accepted review."""
    return ProposalReview.objects.filter(
        proposal=OuterRef("proposal"),
        expected_version__gt=OuterRef("expected_version"),
    ).exclude(decision="approved")


def active_bases():
    """Accepted-base reviews still at their proposal's current source reference
    and not undone by a later decision review.

    A subquery over ``OuterRef`` for a ``ProposedChange`` queryset.
    """
    return ProposalReview.objects.filter(
        proposal=OuterRef("pk"),
        accepted_base=True,
        accepted_source_id=OuterRef("current_source_id"),
    ).exclude(Exists(_undone()))


def with_accepted_base(queryset):
    """Annotate each proposal with ``conflict_accepted`` (an active base).

    The planner (``publication._evaluate``) merges an annotated row whose
    flag is set against ParishSoft's current value instead of the Family's
    baseline. A row read without the annotation is planned against the
    baseline, so it plans a conflict and blocks its entity: missing the
    annotation fails safe.
    """
    return queryset.annotate(conflict_accepted=Exists(active_bases()))


def has_active_base(proposal):
    """Whether one proposal row has an active accepted base."""
    return (
        ProposalReview.objects.filter(
            proposal_id=proposal.pk,
            accepted_base=True,
            accepted_source_id=proposal.current_source_id,
        )
        .exclude(Exists(_undone()))
        .exists()
    )
