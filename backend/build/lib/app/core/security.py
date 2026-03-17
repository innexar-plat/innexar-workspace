"""Password hashing and JWT creation (staff vs customer use different secrets)."""
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Bcrypt truncates at 72 bytes; passlib raises ValueError if exceeded. Truncate explicitly.
_BCRYPT_MAX_BYTES = 72


def _truncate_for_bcrypt(password: str) -> str:
    """Truncate password to 72 bytes (bcrypt limit) to avoid ValueError."""
    encoded = password.encode("utf-8")
    if len(encoded) <= _BCRYPT_MAX_BYTES:
        return password
    return encoded[:_BCRYPT_MAX_BYTES].decode("utf-8", errors="ignore")


def hash_password(password: str) -> str:
    """Hash a plain password."""
    return pwd_context.hash(_truncate_for_bcrypt(password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plain password against hash."""
    return pwd_context.verify(_truncate_for_bcrypt(plain_password), hashed_password)


def create_token_staff(
    subject: str | int,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create JWT for staff (uses SECRET_KEY_STAFF)."""
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode: dict[str, Any] = {
        "sub": str(subject),
        "exp": expire,
        "type": "staff",
    }
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(
        to_encode,
        settings.SECRET_KEY_STAFF,
        algorithm=settings.ALGORITHM,
    )


def create_token_customer(
    subject: str | int,
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create JWT for customer (uses SECRET_KEY_CUSTOMER)."""
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode: dict[str, Any] = {
        "sub": str(subject),
        "exp": expire,
        "type": "customer",
    }
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(
        to_encode,
        settings.SECRET_KEY_CUSTOMER,
        algorithm=settings.ALGORITHM,
    )


def decode_token_staff(token: str) -> dict[str, Any] | None:
    """Decode and validate staff JWT. Returns payload or None if invalid."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY_STAFF,
            algorithms=[settings.ALGORITHM],
        )
        if payload.get("type") != "staff":
            return None
        return payload
    except JWTError:
        return None


def decode_token_customer(token: str) -> dict[str, Any] | None:
    """Decode and validate customer JWT. Returns payload or None if invalid."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY_CUSTOMER,
            algorithms=[settings.ALGORITHM],
        )
        if payload.get("type") != "customer":
            return None
        return payload
    except JWTError:
        return None


def create_openclaw_proxy_token(expires_minutes: int = 10) -> str:
    """Create short-lived JWT for OpenClaw UI proxy (no user). Uses SECRET_KEY_STAFF."""
    expires_delta = timedelta(minutes=expires_minutes)
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode: dict[str, Any] = {
        "sub": "openclaw-proxy",
        "exp": expire,
        "type": "openclaw-proxy",
    }
    return jwt.encode(
        to_encode,
        settings.SECRET_KEY_STAFF,
        algorithm=settings.ALGORITHM,
    )


def decode_openclaw_proxy_token(token: str) -> dict[str, Any] | None:
    """Decode and validate OpenClaw proxy JWT. Returns payload or None if invalid."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY_STAFF,
            algorithms=[settings.ALGORITHM],
        )
        if payload.get("type") != "openclaw-proxy":
            return None
        return payload
    except JWTError:
        return None
