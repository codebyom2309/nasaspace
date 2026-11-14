"""User authentication and session management backed by TiDB."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Any

from . import db

logger = logging.getLogger(__name__)

# In-memory session token store (token -> {user_id, username, display_name, expires_at})
_SESSIONS: dict[str, dict[str, Any]] = {}
SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 days


def _hash_password(password: str, salt: bytes | None = None) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with 100k rounds and random salt."""
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return f"{salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, hash_hex = stored_hash.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        expected_dk = bytes.fromhex(hash_hex)
        test_dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return hmac.compare_digest(test_dk, expected_dk)
    except Exception:
        return False


def register(username: str, password: str, display_name: str = "") -> dict[str, Any]:
    username = username.strip().lower()
    if not username or len(username) < 3 or len(username) > 30:
        return {"error": "Username must be between 3 and 30 characters"}
    if len(password) < 4:
        return {"error": "Password must be at least 4 characters"}

    pw_hash = _hash_password(password)
    disp = (display_name or username).strip()[:50]

    try:
        user_id = db.execute_commit(
            """INSERT INTO users (username, display_name, password_hash)
               VALUES (%s, %s, %s)""",
            (username, disp, pw_hash),
        )
        token = create_session(user_id, username, disp)
        return {"ok": True, "token": token, "user": {"id": user_id, "username": username, "display_name": disp}}
    except Exception as e:
        err_msg = str(e).lower()
        if "duplicate" in err_msg or "unique" in err_msg:
            return {"error": "Username already taken"}
        logger.error("User registration failed: %s", e)
        return {"error": "Registration failed, please try again"}


def login(username: str, password: str) -> dict[str, Any]:
    username = username.strip().lower()
    try:
        row = db.fetch_one("SELECT id, username, display_name, password_hash FROM users WHERE username = %s", (username,))
        if not row or not _verify_password(password, row["password_hash"]):
            return {"error": "Invalid username or password"}

        token = create_session(row["id"], row["username"], row["display_name"])
        return {
            "ok": True,
            "token": token,
            "user": {"id": row["id"], "username": row["username"], "display_name": row["display_name"]},
        }
    except Exception as e:
        logger.error("User login failed: %s", e)
        return {"error": "Login failed"}


def create_session(user_id: int, username: str, display_name: str) -> str:
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = {
        "user_id": user_id,
        "username": username,
        "display_name": display_name,
        "expires_at": time.time() + SESSION_TTL_SECONDS,
    }
    return token


def get_session(token: str | None) -> dict[str, Any] | None:
    if not token or token not in _SESSIONS:
        return None
    session = _SESSIONS[token]
    if time.time() > session["expires_at"]:
        _SESSIONS.pop(token, None)
        return None
    return session


def logout(token: str | None) -> None:
    if token:
        _SESSIONS.pop(token, None)

