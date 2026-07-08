from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import (
    AUTH_ACCESS_TOKEN_AUDIENCE,
    AUTH_ACCESS_TOKEN_EXPIRE_MINUTES,
    AUTH_COOKIE_NAME,
    AUTH_ISSUER,
    AUTH_JWT_ALGORITHM,
    AUTH_JWT_SECRET,
    AUTH_REFRESH_TOKEN_EXPIRE_DAYS,
)
from app.services.auth_store import get_user_by_id

_password_hasher = PasswordHasher()
_bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser(dict[str, Any]):
    @property
    def id(self) -> str:
        return str(self["id"])

    @property
    def email(self) -> str:
        return str(self["email"])


def utc_now() -> datetime:
    return datetime.now(UTC)


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except Exception:
        return False


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_optional_value(value: str | None) -> str | None:
    if not value:
        return None
    return hmac.new(AUTH_JWT_SECRET.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def create_access_token(*, user_id: str, scopes: list[str] | None = None) -> str:
    now = utc_now()
    expires_at = now + timedelta(minutes=AUTH_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "iss": AUTH_ISSUER,
        "aud": AUTH_ACCESS_TOKEN_AUDIENCE,
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_urlsafe(24),
        "scope": " ".join(scopes or ["chats:read", "chats:write", "invoices:read", "invoices:write"]),
    }
    return jwt.encode(payload, AUTH_JWT_SECRET, algorithm=AUTH_JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            AUTH_JWT_SECRET,
            algorithms=[AUTH_JWT_ALGORITHM],
            issuer=AUTH_ISSUER,
            audience=AUTH_ACCESS_TOKEN_AUDIENCE,
        )
    except jwt.PyJWTError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


def require_scope(claims: dict[str, Any], required_scope: str) -> None:
    scopes = str(claims.get("scope", "")).split()
    if required_scope not in scopes:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient scope")


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentUser:
    token = credentials.credentials if credentials else None
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = decode_access_token(token)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")

    user = get_user_by_id(subject)
    if user is None or user.get("disabled_at") is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is not active")

    request.state.auth_claims = claims
    return CurrentUser(user)


def refresh_cookie_settings() -> dict[str, Any]:
    secure = os.getenv("AUTH_COOKIE_SECURE", "true").lower() in {"1", "true", "yes", "on"}
    same_site = os.getenv("AUTH_COOKIE_SAMESITE", "lax")
    return {
        "key": AUTH_COOKIE_NAME,
        "httponly": True,
        "secure": secure,
        "samesite": same_site,
        "max_age": int(timedelta(days=AUTH_REFRESH_TOKEN_EXPIRE_DAYS).total_seconds()),
        "path": "/",
    }
