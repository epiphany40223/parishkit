"""The public About, Privacy policy and Terms of use pages (#2354).

Google requires an application home page, a privacy policy and terms of
service on the app's own domain before the Admin sign-in project can be
published and verified. These three pages are that, for anyone, with no
sign-in. See docs/specs/stewardship/architecture/spec.md, "Public information
pages".

The pages are parish-neutral templates. The parish's name, website and phone
come from the applied parish profile, and the contact email is the outgoing
mail's Reply-to address: values the configuration already validates, so the
pages need no configuration or schema of their own. Before setup or during a
restore review there is no parish to name; the pages still render, saying
"the parish" and leaving the contact lines out.
"""

from datetime import date

from django.db import DatabaseError
from django.shortcuts import render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.web.presentation import parish_date

from .branding_context import display_parish

# When the pages' wording last changed. Change it in the same commit as the
# text (the pages show it as "Last updated"), never for an unrelated edit.
UPDATED = date(2026, 11, 1)

TEMPLATES = {
    "about": "stewardship/public-about.html",
    "privacy": "stewardship/public-privacy.html",
    "terms": "stewardship/public-terms.html",
}


def parish_contact(request):
    """The parish's name and public contact details, or just "the parish".

    ``display_parish`` already returns None on bootstrap, restore-review and
    database-error pages. The Reply-to read is the one extra query; if it
    fails, the page still renders without the email line.
    """
    parish = display_parish(request)
    if parish is None:
        return {"name": _("the parish")}
    # Imported here: page_content pulls in the response and mail modules,
    # which this lightweight public view should not load at import time.
    from parishkit.stewardship.responses.page_content import reply_to

    try:
        email = reply_to(parish.configuration_id)
    except DatabaseError:
        email = ""
    return {
        "name": parish.name,
        "website": parish.website,
        "phone": parish.phone,
        "email": email,
        "known": True,
    }


def _page(request, kind):
    """Render one public page with the parish's contact and the text's date."""
    return render(
        request,
        TEMPLATES[kind],
        {"contact": parish_contact(request), "updated": parish_date(UPDATED)},
    )


@require_http_methods(["GET", "HEAD"])
def about(request):
    """About this site: the application home page Google asks for."""
    return _page(request, "about")


@require_http_methods(["GET", "HEAD"])
def privacy(request):
    """The Privacy policy: what the site does with people's information."""
    return _page(request, "privacy")


@require_http_methods(["GET", "HEAD"])
def terms(request):
    """The Terms of use: short, plain terms for the parish's own site."""
    return _page(request, "terms")
