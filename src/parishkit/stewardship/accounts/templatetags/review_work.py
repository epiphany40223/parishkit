"""My review work's panels, for its page and Admin Home (#1690)."""

from django import template

from parishkit.stewardship.accounts import review_work

register = template.Library()


@register.simple_tag(takes_context=True)
def review_panels(context, review):
    """Return ``review_work.panels`` for this render and ``review``; see that function.

    A tag rather than a view value because the panels follow the viewer's
    menu, which the chrome's context processor builds at render time, and
    Admin Home's counts are read by that processor's open-count statement
    (``review_work.with_counts``).
    """
    review = review_work.with_counts(getattr(context, "request", None), review or {})
    return review_work.panels(context.get("admin_chrome") or {}, review)


@register.simple_tag(takes_context=True)
def review_wizard_cards(context, review):
    """Return ``review_work.wizard_cards`` for this render and ``review`` (#2123)."""
    return review_work.wizard_cards(context.get("admin_chrome") or {}, review or {})


@register.simple_tag(takes_context=True)
def leader_review_steps(context, review):
    """Return ``review_work.leader_steps`` for this render's viewer (#2405).

    A Ministry leader's two steps on My review work, or None for anyone
    else. A tag, as the panels are, because the choices follow the viewer's
    menu, which the chrome's context processor builds at render time.
    """
    return review_work.leader_steps(
        context.get("admin_chrome") or {}, review or {}, context.get("principal")
    )
