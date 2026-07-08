from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from app.config import AUTH_COOKIE_NAME
from app.services.auth_schema import ensure_auth_schema
from app.services.auth_security import (
    CurrentUser,
    create_access_token,
    get_current_user,
    hash_optional_value,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    refresh_cookie_settings,
    verify_password,
)
from app.services.auth_store import (
    DuplicateEmailError,
    create_user,
    create_user_session,
    get_active_session_by_refresh_hash,
    get_user_by_email,
    get_user_by_id,
    public_user,
    revoke_other_sessions,
    revoke_session,
    revoke_session_by_refresh_hash,
    update_user_email,
    update_user_password,
)
from app.services.password_policy import PasswordPolicyError, validate_password_policy

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    display_name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UpdateEmailRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_email: EmailStr


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict[str, Any]


def _validate_password_or_422(password: str) -> None:
    try:
        validate_password_policy(password)
    except PasswordPolicyError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=error.errors) from error


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(value=refresh_token, **refresh_cookie_settings())


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/")


def _client_hashes(request: Request) -> tuple[str | None, str | None]:
    user_agent_hash = hash_optional_value(request.headers.get("user-agent"))
    ip_hash = hash_optional_value(request.client.host if request.client else None)
    return user_agent_hash, ip_hash


def _issue_auth_response(request: Request, response: Response, user: dict[str, Any]) -> AuthResponse:
    refresh_token = new_refresh_token()
    user_agent_hash, ip_hash = _client_hashes(request)
    create_user_session(
        user_id=user["id"],
        refresh_token_hash=hash_refresh_token(refresh_token),
        user_agent_hash=user_agent_hash,
        ip_hash=ip_hash,
    )
    _set_refresh_cookie(response, refresh_token)
    return AuthResponse(
        access_token=create_access_token(user_id=user["id"]),
        expires_in=15 * 60,
        user=public_user(user),
    )


def _current_refresh_session(refresh_token_cookie: str | None) -> dict[str, Any] | None:
    if not refresh_token_cookie:
        return None
    return get_active_session_by_refresh_hash(hash_refresh_token(refresh_token_cookie))


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, response: Response) -> AuthResponse:
    ensure_auth_schema()
    _validate_password_or_422(payload.password)
    try:
        user = create_user(
            email=str(payload.email),
            password_hash=hash_password(payload.password),
            display_name=payload.display_name.strip() if payload.display_name else None,
        )
    except DuplicateEmailError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Could not register with these credentials") from error
    return _issue_auth_response(request, response, user)


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, request: Request, response: Response) -> AuthResponse:
    ensure_auth_schema()
    user = get_user_by_email(str(payload.email))
    if user is None or user.get("disabled_at") is not None or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return _issue_auth_response(request, response, user)


@router.post("/refresh", response_model=AuthResponse)
def refresh_token(
    request: Request,
    response: Response,
    refresh_token_cookie: str | None = Cookie(default=None, alias=AUTH_COOKIE_NAME),
) -> AuthResponse:
    ensure_auth_schema()
    if not refresh_token_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token required")

    token_hash = hash_refresh_token(refresh_token_cookie)
    session = get_active_session_by_refresh_hash(token_hash)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token is invalid")

    user = get_user_by_id(session["user_id"])
    if user is None or user.get("disabled_at") is not None:
        revoke_session(session["id"])
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is not active")

    revoke_session(session["id"])
    return _issue_auth_response(request, response, user)


@router.post("/logout")
def logout(
    response: Response,
    refresh_token_cookie: str | None = Cookie(default=None, alias=AUTH_COOKIE_NAME),
) -> dict[str, str]:
    if refresh_token_cookie:
        revoke_session_by_refresh_hash(hash_refresh_token(refresh_token_cookie))
    _clear_refresh_cookie(response)
    return {"status": "logged_out"}


@router.get("/me")
def me(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    return public_user(current_user)


@router.patch("/me/email")
def update_email(
    payload: UpdateEmailRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    ensure_auth_schema()
    user = get_user_by_id(current_user.id)
    if user is None or not verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is invalid")

    try:
        updated_user = update_user_email(user_id=current_user.id, email=str(payload.new_email))
    except DuplicateEmailError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Could not update email") from error
    return public_user(updated_user)


@router.patch("/me/password")
def change_password(
    payload: ChangePasswordRequest,
    refresh_token_cookie: str | None = Cookie(default=None, alias=AUTH_COOKIE_NAME),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, str]:
    ensure_auth_schema()
    user = get_user_by_id(current_user.id)
    if user is None or not verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is invalid")

    _validate_password_or_422(payload.new_password)
    update_user_password(user_id=current_user.id, password_hash=hash_password(payload.new_password))

    current_session = _current_refresh_session(refresh_token_cookie)
    keep_session_id = current_session["id"] if current_session and current_session["user_id"] == current_user.id else None
    revoke_other_sessions(user_id=current_user.id, keep_session_id=keep_session_id)
    return {"status": "password_changed"}
