"""Users and access group URLs: the Users page and its actions (#952).

The Users page lists everyone who may sign in to the Admin portal. New user
and Edit user (the user's opaque record ID) are pages; Delete answers the
table's confirmation dialog and the Name lookup answers the New page's
script, so neither has a slashless form; nor has the Import Ministry
leaders review, which answers only the leaders table's POST (#952 slice 6).
The page names come from the admin-portal specification's placement table
("Page names and placement").
The old Portal users addresses (``users/rules`` and its autosave routes)
are not kept (#864).
"""

from django.urls import path

from ..accounts import leader_import_views, user_views

patterns = [
    path("users/", user_views.users, name="users"),
    path("users/new/", user_views.user_new, name="user_new"),
    # The Name lookup is a read-only POST from New user's script (JSON).
    path(
        "users/new/names/",
        user_views.user_name_suggestions,
        name="user_name_suggestions",
    ),
    path("users/deletion/", user_views.user_delete, name="user_delete"),
    # The Import Ministry leaders review answers the leaders table's
    # Import selected and Import all (#952 slice 6); its Apply posts to Users.
    path(
        "users/leaders/import/",
        leader_import_views.user_leader_import,
        name="user_leader_import",
    ),
    path("users/<uuid:user_id>/edit/", user_views.user_edit, name="user_edit"),
]
