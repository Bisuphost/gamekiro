from .base import *
from .base import env

DEBUG = False

SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 7
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Django 5.1+ replaced STATICFILES_STORAGE/DEFAULT_FILE_STORAGE with the STORAGES dict.
# The old settings are silently ignored on Django 6.0 (no back-compat shim) — using them
# left prod serving static/media from local disk instead of WhiteNoise/R2.
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

AWS_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID", default=None)
AWS_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY", default=None)
AWS_STORAGE_BUCKET_NAME = env("R2_BUCKET_NAME", default=None)
AWS_S3_ENDPOINT_URL = env("R2_ENDPOINT_URL", default=None)
AWS_S3_CUSTOM_DOMAIN = env("R2_PUBLIC_DOMAIN", default=None)
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = False

# EMAIL_BACKEND defaults to the console backend (base.py) and prod.py never overrode it,
# so order/key-delivery emails would silently print to the log instead of sending.
# Require SMTP to be explicitly configured in production.
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "marketplace": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

SENTRY_DSN = env("SENTRY_DSN", default=None)
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    from gamekiro.sentry_scrub import scrub_sentry_event

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
        before_send=scrub_sentry_event,
    )
