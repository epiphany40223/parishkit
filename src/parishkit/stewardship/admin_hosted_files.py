"""``files list``: the hosted file library of the Admin command line (ADM-11).

The command reads the Hosted files page (``accounts.hosted_file_views``,
#346) through the page's own pieces: its capability (``CONFIGURE``, so
Administrators only), its read of the library in placeholder-name order and
its row builder (``_rows``: each file's link, placeholder, image tag, uses
and Missing flag), so the page and the command line cannot drift.

It admits passively, so a read-only session reads, and records nothing, as
the page's GET records nothing. Hosted files are public by design (the page
warns never to upload anything private), so each file's public link is
printed as the page shows it. ``files delete`` and ``files rename`` come
later in PR 10.
"""

from dataclasses import dataclass

from .admin_reads import ReadModel, _held, _recheck


@dataclass(frozen=True)
class HostedFileLibrary(ReadModel):
    """The library as the page lists it, and its usage line.

    ``files`` is sorted by placeholder name (``slug``), the page's default
    sort. Each file has ``id``, ``name`` (the uploaded file name), ``slug``,
    ``placeholder`` (``{{ file.slug }}``), ``image_tag`` (the page's
    copyable tag; "" for a document), ``type`` (the stored kind, such as
    ``pdf`` or ``png``), ``width`` and ``height`` (None for a document),
    ``size`` in bytes, ``link`` (the public link), ``missing`` (the stored
    copy is gone from the server) and ``uses`` (``source`` and the page's
    plain-words ``description``, in the page's order). ``count`` and
    ``total_bytes`` are the library's totals; ``max_files`` and
    ``max_bytes`` its limits.
    """

    count: int
    total_bytes: int
    max_files: int
    max_bytes: int
    files: list


def file_model(row):
    """One page row (``hosted_file_views._rows``) as the command prints it."""
    stored = row["file"]
    return {
        "id": stored.pk,
        "name": stored.original_name,
        "slug": stored.slug,
        "placeholder": row["placeholder"],
        "image_tag": row["image_tag"],
        "type": stored.kind,
        "width": stored.width,
        "height": stored.height,
        "size": stored.size,
        "link": row["link"],
        "missing": row["missing"],
        "uses": [
            {"source": use.source, "description": str(use.description)}
            for use in row["uses"]
        ],
    }


def read_library(caller, service):
    """``files list``: the page's library, Administrators only, any session.

    Admits passively with the page's ``CONFIGURE`` capability, reads the
    library and its uses in one read transaction as the page does, and
    rechecks the session before reporting. A configuration refusal (such as
    no media root) is ``unavailable`` through ``_held``.
    """
    from .accounts.hosted_file_models import MAX_FILES, MAX_LIBRARY_BYTES, HostedFile
    from .accounts.hosted_file_views import _rows
    from .accounts.policy import Capability
    from .admin_reads import _admit
    from .campaigns.work_locks import read_transaction

    def step():
        """Admit, read the library in the page's order, recheck."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        with read_transaction():
            files = list(HostedFile.objects.order_by("slug"))
            rows = _rows(files)
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return HostedFileLibrary(
            count=len(files),
            total_bytes=sum(row.size for row in files),
            max_files=MAX_FILES,
            max_bytes=MAX_LIBRARY_BYTES,
            files=[file_model(row) for row in rows],
        )

    return _held(step)
