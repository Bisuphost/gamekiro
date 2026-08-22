from .base import *  # noqa: F401,F403
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

DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"
AWS_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID", default=None)
AWS_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY", default=None)
AWS_STORAGE_BUCKET_NAME = env("R2_BUCKET_NAME", default=None)
AWS_S3_ENDPOINT_URL = env("R2_ENDPOINT_URL", default=None)
AWS_S3_CUSTOM_DOMAIN = env("R2_PUBLIC_DOMAIN", default=None)
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = False

SENTRY_DSN = env("SENTRY_DSN", default=None)
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
