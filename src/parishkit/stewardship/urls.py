"""Namespaced scaffold; ingress isolation is additionally owned by ARC-03/OPS-01."""

from django.urls import include, path

from . import views

public_patterns = [
    path("", views.unavailable, name="entry"),
    path("access/<str:token>", views.unavailable, name="access"),
]
family_patterns = [path("", views.unavailable, name="entry")]
admin_patterns = [
    path("", views.unavailable, name="index"),
    path("login", views.unavailable, name="login"),
]
internal_patterns = [
    path("health/live", views.live, name="live"),
    path("health/ready", views.ready, name="ready"),
    path("metrics", views.metrics, name="metrics"),
]
urlpatterns = [
    path("", include((public_patterns, "public"))),
    path("family/", include((family_patterns, "family"))),
    path("admin/", include((admin_patterns, "admin"))),
    path("", include((internal_patterns, "internal"))),
]
