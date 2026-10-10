"""Admin URL patterns, one module per menu group (admin-portal spec, "URL scheme").

Each group's module lists its pages under ``/admin/<group>/`` and the form
actions, fragments and downloads that move with them; ``slashless`` lists
each page's form without its trailing slash. Old addresses are not kept
(#864). ``parishkit.stewardship.urls`` includes them in the ``admin``
namespace. The remaining groups move here with their URL slices (ADM-12,
NAV-7 to NAV-12); until then their routes stay in ``urls.py``.
"""
