"""Shared Django settings, with no provider credentials or writable defaults."""

DEBUG = False
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ROOT_URLCONF = "parishkit.stewardship.urls"
WSGI_APPLICATION = "parishkit.stewardship.wsgi.application"
ALLOWED_HOSTS = []
INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "parishkit.stewardship.accounts",
    "parishkit.stewardship.source",
    "parishkit.stewardship.campaigns",
    "parishkit.stewardship.responses",
    "parishkit.stewardship.workflows",
    "parishkit.stewardship.reports",
    "parishkit.stewardship.jobs",
    "parishkit.stewardship.audit",
]
# Deliberately no Django admin/password authentication. ARC-04 owns identity.
MIDDLEWARE = [
    "parishkit.stewardship.observability.CorrelationMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "no-referrer"
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
LOGGING_CONFIG = "parishkit.stewardship.observability.configure_logging"
LOGGING = {"version": 1}
# Until ARC-02 installs deployment configuration, any accidental DB operation
# fails instead of silently creating a SQLite file in the checkout.
DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
