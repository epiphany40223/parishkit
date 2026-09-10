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
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "parishkit.stewardship.accounts",
    "parishkit.stewardship.source",
    "parishkit.stewardship.campaigns",
    "parishkit.stewardship.responses",
    "parishkit.stewardship.workflows",
    "parishkit.stewardship.reports",
    "parishkit.stewardship.jobs",
    "parishkit.stewardship.audit",
]
# Django auth tables support allauth internals; no password backend or routes.
MIDDLEWARE = [
    "parishkit.stewardship.observability.CorrelationMiddleware",
    "parishkit.stewardship.web.security.SecurityBoundaryMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "parishkit.stewardship.accounts.authentication.AuthLimitMiddleware",
    "parishkit.stewardship.accounts.sessions.NamespacedSessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "parishkit.stewardship.accounts.access_gate.AccessGateMiddleware",
    "allauth.account.middleware.AccountMiddleware",
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
STATIC_URL = "/static/"
STEWARDSHIP_PROXY_HOPS = 0
STEWARDSHIP_TRUSTED_PROXY_NETWORKS = ()
STEWARDSHIP_INTERNAL_NETWORKS = ("127.0.0.0/8", "::1/128")
CSRF_FAILURE_VIEW = "parishkit.stewardship.web.security.csrf_failure"
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 500
DATA_UPLOAD_MAX_NUMBER_FILES = 4
AUTHENTICATION_BACKENDS = []
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
SOCIALACCOUNT_ADAPTER = "parishkit.stewardship.accounts.authentication.GoogleBoundary"
SOCIALACCOUNT_STORE_TOKENS = False
SOCIALACCOUNT_PROVIDERS = {
    "google": {"SCOPE": ["openid", "email"], "OAUTH_PKCE_ENABLED": True},
}
# OPS-04 supplies a validated Runtime; missing means unavailable, never a bypass.
STEWARDSHIP_AUTH_RUNTIME = None
STEWARDSHIP_FAMILY_RUNTIME = None
