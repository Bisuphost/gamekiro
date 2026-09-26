import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from marketplace.models import KeyAccessLog


def _fernet():
    key = settings.MARKETPLACE_KEY_ENC_KEY
    if not key:
        raise ImproperlyConfigured(
            "MARKETPLACE_KEY_ENC_KEY is not set — cannot encrypt or decrypt game keys."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext):
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext):
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored key could not be decrypted.") from exc


def fingerprint(plaintext):
    cleaned = "".join(plaintext.split()).upper()
    return hashlib.sha256(cleaned.encode()).hexdigest()


def mask(plaintext):
    cleaned = "".join(plaintext.split()).upper()
    if len(cleaned) <= 4:
        return "X" * len(cleaned)
    tail = cleaned[-4:]
    hidden_length = len(cleaned) - 4
    hidden_groups = max(1, min(hidden_length // 4, 3))
    return "-".join(["XXXX"] * hidden_groups + [tail])


def record_access(user, key, order, action, ip_address=None, user_agent=""):
    return KeyAccessLog.objects.create(
        user=user,
        key=key,
        order=order,
        action=action,
        ip_address=ip_address,
        user_agent=user_agent[:255],
    )
