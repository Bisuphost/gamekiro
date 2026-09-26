from .base import *
from .base import env

DEBUG = True

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS += ["django_extensions"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

MARKETPLACE_ALLOW_MOCK = True
PAYMENT_PROVIDERS_ENABLED = env.list("PAYMENT_PROVIDERS_ENABLED", default=["mock"])

AXES_ENABLED = False
