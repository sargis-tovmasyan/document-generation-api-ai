from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.database import database_connection

DEFAULT_USER_ID = "default_user"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)
