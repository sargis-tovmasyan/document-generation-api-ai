from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.config import AUTH_REFRESH_TOKEN_EXPIRE_DAYS
from app.database import database_connection


class DuplicateEmailError(ValueError):
    pass


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def sqlite_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def row_to_user(row: Any) -> dict[str, Any]:
    return dict(row)


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user.get("display_name"),
        "email_verified_at": user.get("email_verified_at"),
        "created_at": user.get("created_at"),
    }


def create_user(*, email: str, password_hash: str, display_name: str | None = None) -> dict[str, Any]:
    user_id = new_id("user")
    normalized_email = normalize_email(email)
    with database_connection() as connection:
        existing = connection.execute(
            "SELECT id FROM users WHERE email_normalized = ?",
            (normalized_email,),
        ).fetchone()
        if existing is not None:
            raise DuplicateEmailError("A user with this email already exists")

        connection.execute(
            """
            INSERT INTO users (id, email, email_normalized, password_hash, display_name)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, email.strip(), normalized_email, password_hash, display_name),
        )
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return row_to_user(row)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with database_connection() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE email_normalized = ?",
            (normalize_email(email),),
        ).fetchone()
    return row_to_user(row) if row is not None else None


def get_user_by_id(user_id: str) -> dict[str, Any] | None:
    with database_connection() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return row_to_user(row) if row is not None else None


def create_user_session(
    *,
    user_id: str,
    refresh_token_hash: str,
    user_agent_hash: str | None = None,
    ip_hash: str | None = None,
) -> dict[str, Any]:
    session_id = new_id("session")
    expires_at = datetime.now(UTC) + timedelta(days=AUTH_REFRESH_TOKEN_EXPIRE_DAYS)
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO user_sessions (id, user_id, refresh_token_hash, user_agent_hash, ip_hash, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, user_id, refresh_token_hash, user_agent_hash, ip_hash, sqlite_timestamp(expires_at)),
        )
        row = connection.execute("SELECT * FROM user_sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row)


def get_active_session_by_refresh_hash(refresh_token_hash: str) -> dict[str, Any] | None:
    with database_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM user_sessions
            WHERE refresh_token_hash = ?
              AND revoked_at IS NULL
              AND expires_at > CURRENT_TIMESTAMP
            """,
            (refresh_token_hash,),
        ).fetchone()
    return dict(row) if row is not None else None


def touch_session(session_id: str) -> None:
    with database_connection() as connection:
        connection.execute(
            "UPDATE user_sessions SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
            (session_id,),
        )


def revoke_session(session_id: str) -> None:
    with database_connection() as connection:
        connection.execute(
            """
            UPDATE user_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE id = ? AND revoked_at IS NULL
            """,
            (session_id,),
        )


def revoke_session_by_refresh_hash(refresh_token_hash: str) -> None:
    with database_connection() as connection:
        connection.execute(
            """
            UPDATE user_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE refresh_token_hash = ? AND revoked_at IS NULL
            """,
            (refresh_token_hash,),
        )
