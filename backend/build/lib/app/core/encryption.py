"""Fernet encryption for integration secrets."""
import base64
import logging

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.core.config import settings

logger = logging.getLogger(__name__)


def _get_fernet() -> Fernet | None:
    """Build Fernet from ENCRYPTION_KEY (base64) or derive from SECRET_KEY_STAFF if not set."""
    key_b64 = settings.ENCRYPTION_KEY
    if key_b64:
        try:
            return Fernet(key_b64.encode() if isinstance(key_b64, str) else key_b64)
        except Exception as e:
            logger.warning("Invalid ENCRYPTION_KEY: %s", e)
            return None
    # Fallback: derive from SECRET_KEY_STAFF (not ideal for production)
    secret = (settings.SECRET_KEY_STAFF or "default-secret").encode()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"innexar_workspace_integration",
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret))
    return Fernet(key)


def encrypt_value(plain: str) -> str | None:
    """Encrypt string; returns base64 ciphertext or None if encryption unavailable."""
    f = _get_fernet()
    if not f:
        return None
    try:
        return f.encrypt(plain.encode()).decode()
    except Exception as e:
        logger.warning("Encrypt failed: %s", e)
        return None


def decrypt_value(cipher: str | None) -> str | None:
    """Decrypt base64 ciphertext; returns plaintext or None."""
    if not cipher:
        return None
    f = _get_fernet()
    if not f:
        return None
    try:
        return f.decrypt(cipher.encode()).decode()
    except InvalidToken:
        return None
    except Exception as e:
        logger.warning("Decrypt failed: %s", e)
        return None


def mask_value(value: str | None) -> str:
    """Return masked string for API (e.g. sk_***xyz)."""
    if not value or len(value) < 8:
        return "***"
    return value[:4] + "***" + value[-4:] if len(value) > 8 else "***"
