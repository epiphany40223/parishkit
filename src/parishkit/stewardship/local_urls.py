"""LOCAL-only URL configuration: every production route plus the test sign-in.

``configure_web`` selects this module for the LOCAL profile alone (#476);
``urls.py`` never imports it, so no other profile can resolve the route.
"""

from django.urls import path

from .accounts import local_sign_in
from .urls import urlpatterns as production_patterns

urlpatterns = [
    path(local_sign_in.PATH[1:], local_sign_in.sign_in, name="local_sign_in"),
    *production_patterns,
]
