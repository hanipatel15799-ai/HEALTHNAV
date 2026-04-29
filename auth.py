"""
auth.py — HealthNav authentication (v2.1.0)

Change vs zip: verify_user_credentials() now accepts username OR email.
Everything else is identical to healthnav_aws_fixed.zip.

Why this matters:
  A user who registers with username='hani', email='hani@gmail.com'
  expects to log back in with either. Previously only 'hani' worked —
  'hani@gmail.com' returned 401, making it look like their account was gone.
  Their data was always safe; they just couldn't reach it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from typing import Any, Dict, Optional

import bcrypt
from dotenv import load_dotenv

from patient_record_retrieval import get_connection

load_dotenv()
logger = logging.getLogger(__name__)

SESSION_SECRET   = os.getenv("APP_SECRET", "change-this-secret-in-env").strip()
SESSION_TTL_SECS = int(os.getenv("SESSION_TTL_SECS", "604800"))
ENVIRONMENT      = os.getenv("ENVIRONMENT", "development").strip().lower()

if SESSION_SECRET in {"change-this-secret-in-env", "", "replace-with-a-strong-random-secret"}:
    _msg = (
        "APP_SECRET is a placeholder. Generate: "
        "python -c \"import secrets; print(secrets.token_hex(32))\""
    )
    if ENVIRONMENT in {"production", "prod", "staging"}:
        raise RuntimeError(_msg)
    logger.warning(_msg)

_BCRYPT_ROUNDS = 12


# ── Password ──────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    ).decode("utf-8")


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    if stored_hash.startswith(("$2b$", "$2a$", "$2y$")):
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"),
                stored_hash.replace("$2y$", "$2b$", 1).encode("utf-8"),
            )
        except Exception as exc:
            logger.warning("bcrypt verify failed: %s", exc)
            return False
    logger.warning("Legacy hash — user must re-register. prefix=%s", stored_hash[:8])
    return False


def hash_patient_id_for_log(patient_id: str) -> str:
    return hashlib.sha256((patient_id or "").encode()).hexdigest()[:12] if patient_id else "unknown"


# ── Session ───────────────────────────────────────────────────────────────────

def _sign_value(value: str) -> str:
    return hmac.new(
        SESSION_SECRET.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session_token(
    user_id: str, username: str, patient_id: str, role: str,
    ttl_secs: Optional[int] = None,
) -> str:
    ttl_secs = ttl_secs or SESSION_TTL_SECS
    exp = int(time.time()) + ttl_secs
    payload = {
        "jti": secrets.token_urlsafe(24),
        "user_id": str(user_id), "username": username,
        "patient_id": patient_id, "role": role or "patient", "exp": exp,
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    token = f"{payload_json}.{_sign_value(payload_json)}"

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO auth_sessions
                        (session_token,user_id,username,patient_id,role,expires_at,is_revoked)
                       VALUES (%s,%s,%s,%s,%s,TO_TIMESTAMP(%s),FALSE)
                       ON CONFLICT (session_token) DO NOTHING;""",
                    (_hash_token(token), payload["user_id"], username,
                     patient_id, role or "patient", exp),
                )
    finally:
        conn.close()
    return token


def get_current_session(session_token: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Validates cookie. Returns session dict or None.
    None = 401. User data is ALWAYS preserved in DB — they just need to re-login.
    """
    if not session_token or "." not in session_token:
        return None

    last_dot     = session_token.rfind(".")
    payload_json = session_token[:last_dot]
    sig          = session_token[last_dot + 1:]

    if not hmac.compare_digest(sig, _sign_value(payload_json)):
        return None

    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return None

    if int(time.time()) > int(payload.get("exp", 0)):
        return None  # expired — re-login restores same patient_id + all data

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT user_id,username,patient_id,role
                   FROM auth_sessions
                   WHERE session_token=%s AND is_revoked=FALSE AND expires_at>NOW();""",
                (_hash_token(session_token),),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {"user_id": str(row[0]), "username": row[1],
                    "patient_id": row[2], "role": row[3] or "patient"}
    finally:
        conn.close()


def invalidate_session_token(session_token: str) -> None:
    if not session_token:
        return
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth_sessions SET is_revoked=TRUE WHERE session_token=%s;",
                    (_hash_token(session_token),),
                )
    except Exception as exc:
        logger.warning("invalidate_session_token failed: %s", exc)
    finally:
        conn.close()


def cleanup_expired_sessions() -> None:
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM auth_sessions WHERE expires_at<NOW() OR is_revoked=TRUE;"
                )
                logger.info("cleanup_expired_sessions: removed %d rows", cur.rowcount)
    except Exception as exc:
        logger.warning("cleanup_expired_sessions failed (non-fatal): %s", exc)
    finally:
        conn.close()


# ── Tables ────────────────────────────────────────────────────────────────────

def ensure_auth_support_tables() -> None:
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        session_token TEXT PRIMARY KEY,
                        user_id       TEXT NOT NULL,
                        username      TEXT NOT NULL,
                        patient_id    TEXT NOT NULL,
                        role          TEXT NOT NULL,
                        expires_at    TIMESTAMPTZ NOT NULL,
                        created_at    TIMESTAMPTZ DEFAULT NOW(),
                        is_revoked    BOOLEAN DEFAULT FALSE
                    );
                    CREATE INDEX IF NOT EXISTS idx_auth_sessions_patient_id
                        ON auth_sessions(patient_id);
                    CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires_at
                        ON auth_sessions(expires_at);
                """)
        logger.info("auth_sessions table verified.")
    except Exception as exc:
        logger.error("ensure_auth_support_tables failed: %s", exc)
        raise
    finally:
        conn.close()


# ── User management ───────────────────────────────────────────────────────────

def verify_user_credentials(login_id: str, password: str) -> Optional[Dict[str, str]]:
    """
    FIX: Accepts username OR email in login_id.

    A user who registered with username='hani', email='hani@gmail.com'
    can login with either value. Their patient_id is always the same,
    so all their labs/visits/files come back regardless of which they use.
    """
    login_id = login_id.strip()
    if not login_id:
        return None

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, username, patient_id, password_hash,
                          COALESCE(role,'patient'), COALESCE(is_active,TRUE)
                   FROM patient_users
                   WHERE username=%s OR (email=%s AND email IS NOT NULL AND email != '');""",
                (login_id, login_id.lower()),
            )
            row = cur.fetchone()
            if not row:
                return None
            user_id, db_username, patient_id, stored_hash, role, is_active = row
            if not is_active:
                return None
            if not verify_password(password, stored_hash):
                return None
            return {
                "user_id":    str(user_id),
                "username":   db_username,
                "patient_id": patient_id,
                "role":       role or "patient",
            }
    finally:
        conn.close()


def username_exists(username: str) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM patient_users WHERE username=%s;", (username.strip(),))
            return cur.fetchone() is not None
    finally:
        conn.close()


def email_exists(email: str) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM patient_users WHERE email=%s;", (email.strip().lower(),))
            return cur.fetchone() is not None
    finally:
        conn.close()


def create_demo_user_if_missing() -> None:
    demo_username   = os.getenv("DEMO_USERNAME", "").strip()
    demo_password   = os.getenv("DEMO_PASSWORD", "").strip()
    demo_role       = os.getenv("DEMO_ROLE", "patient").strip()
    demo_patient_id = os.getenv("DEMO_PATIENT_ID", "demo-patient-001").strip()

    if not demo_username or not demo_password:
        return
    if demo_password.lower() in {"replace-with-strong-demo-password", "changeme123!", "change-me", ""}:
        logger.warning("DEMO_PASSWORD is placeholder — demo user not created.")
        return

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM patient_users WHERE username=%s;", (demo_username,))
            if cur.fetchone():
                return
    finally:
        conn.close()

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO patient_users
                        (username,email,full_name,patient_id,password_hash,is_active,role)
                       VALUES (%s,%s,%s,%s,%s,TRUE,%s) ON CONFLICT (username) DO NOTHING;""",
                    (demo_username, f"{demo_username}@healthnav.local",
                     demo_username.capitalize(), demo_patient_id,
                     hash_password(demo_password), demo_role),
                )
                cur.execute(
                    "INSERT INTO patient_profiles (patient_id,full_name)"
                    " VALUES (%s,%s) ON CONFLICT (patient_id) DO NOTHING;",
                    (demo_patient_id, demo_username.capitalize()),
                )
        logger.info("Demo user '%s' created.", demo_username)
    except Exception as exc:
        logger.error("Failed to create demo user: %s", exc)
    finally:
        conn.close()
