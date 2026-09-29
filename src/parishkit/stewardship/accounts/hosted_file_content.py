"""Hosted files in page and email content (#346): links and save-time checks.

``{{ file.<slug> }}`` expands to the file's public link when content renders
(``links_for``/``hosted_links``). Content being saved must name existing
files, show only images inline, and describe each image; a configuration
request holds the files its content names until it commits
(``pin_references``), so a deletion cannot slip in between.
"""

import json
import re

from django.db import connection
from django.utils.translation import gettext as _

from parishkit.config import ConfigError
from parishkit.stewardship.web.content import (
    FILE_PLACEHOLDER,
    HostedLinks,
    file_references,
    image_references,
)

from .hosted_file_models import IMAGE_KINDS, HostedFile
from .hosted_files import link

# A raw hosted-file link in content, as the Admin page shows it.
FILE_LINK = re.compile(r"/files/([A-Za-z0-9_-]{43})")


def links_for(origin, *values):
    """``hosted_links(origin)`` if any of ``values`` names a file, else None.

    Rendering content without file placeholders costs no query.
    """
    if any(value and file_references(value) for value in values):
        return hosted_links(origin or "")
    return None


def hosted_links(origin):
    """Every file's public link, for rendering ``{{ file.<slug> }}``."""
    return HostedLinks(
        origin,
        {
            slug: link(origin, token)
            for slug, token in HostedFile.objects.values_list("slug", "token")
        },
    )


def content_problems(html, text):
    """Plain-language problems with the hosted files content names.

    Used when content is saved: every placeholder must name an existing
    file, an inline image must name an image, and needs alt text.
    """
    slugs = file_references(html) | file_references(text)
    images, missing_alt = image_references(html)
    if not slugs | images:
        return []
    kinds = dict(
        HostedFile.objects.filter(slug__in=slugs | images).values_list("slug", "kind")
    )
    problems = [
        _("{{ file.%(slug)s }} does not match any hosted file.") % {"slug": slug}
        for slug in sorted((slugs | images) - kinds.keys())
    ]
    problems += [
        _(
            "{{ file.%(slug)s }} is a document, not an image; link to it with "
            "<a href> instead."
        )
        % {"slug": slug}
        for slug in sorted(images & kinds.keys())
        if kinds[slug] not in IMAGE_KINDS
    ]
    if missing_alt:
        problems.append(
            _('Describe each image with alt text, or use alt="" if it is decorative.')
        )
    return problems


def pin_references(patch):
    """Hold every file a configuration request's content names until it commits.

    Called in the request's own transaction: ``FOR KEY SHARE`` conflicts
    with a deletion's or rename's row lock, so a file cannot disappear
    between this check and the pending request becoming visible to the
    in-use guard. Both placeholders and raw ``/files/<token>`` links count,
    as they do for the guard. A placeholder with no file refuses the
    request; a link to a file that is already gone does not (it only shows
    the unavailable page).
    """
    text = json.dumps(patch, ensure_ascii=False)
    slugs = set(FILE_PLACEHOLDER.findall(text))
    tokens = set(FILE_LINK.findall(text))
    if not slugs and not tokens:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT slug FROM public.stewardship_hosted_file "
            "WHERE slug = ANY(%s) OR token = ANY(%s) ORDER BY id FOR KEY SHARE",
            [sorted(slugs), sorted(tokens)],
        )
        found = {row[0] for row in cursor.fetchall()}
    if slugs - found:
        raise ConfigError("Content names a hosted file that does not exist.")
