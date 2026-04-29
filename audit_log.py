"""
Security-improved audit logging.
Stores only hashed patient identifiers, not raw patient_id.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from config import get_database_config

AUDIT_LOG_DIR = Path(os.getenv("AUDIT_LOG_DIR", "audit"))
AUDIT_LOG_FILE = AUDIT_LOG_DIR / "audit.log"
AUDIT_MAX_BYTES = int(os.getenv("AUDIT_MAX_BYTES", str(10 * 1024 * 1024)))
AUDIT_BACKUP_COUNT = int(os.getenv("AUDIT_BACKUP_COUNT", "10"))

_audit_logger: Optional[logging.Logger] = None


def _hash_value(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _get_audit_logger() -> logging.Logger:
    global _audit_logger
    if _audit_logger is not None:
        return _audit_logger

    AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("healthnav.audit")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(
        AUDIT_LOG_FILE,
        maxBytes=AUDIT_MAX_BYTES,
        backupCount=AUDIT_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    _audit_logger = logger
    return logger


def _write_to_db(entry: dict) -> None:
    try:
        import psycopg2
        conn = psycopg2.connect(**get_database_config().as_psycopg_kwargs())
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS audit_log (
                            id SERIAL PRIMARY KEY,
                            event_time TIMESTAMPTZ NOT NULL,
                            event_type TEXT NOT NULL,
                            patient_hash TEXT,
                            user_role TEXT,
                            action TEXT NOT NULL,
                            phi_detected BOOLEAN DEFAULT FALSE,
                            phi_types TEXT,
                            query_hash TEXT,
                            outcome TEXT,
                            details TEXT
                        );
                        """
                    )
                    cur.execute(
                        """
                        INSERT INTO audit_log (
                            event_time, event_type, patient_hash, user_role,
                            action, phi_detected, phi_types, query_hash,
                            outcome, details
                        ) VALUES (
                            %(event_time)s, %(event_type)s, %(patient_hash)s, %(user_role)s,
                            %(action)s, %(phi_detected)s, %(phi_types)s, %(query_hash)s,
                            %(outcome)s, %(details)s
                        );
                        """,
                        {
                            "event_time": entry["timestamp"],
                            "event_type": entry["event_type"],
                            "patient_hash": entry.get("patient_hash"),
                            "user_role": entry.get("role"),
                            "action": entry["action"],
                            "phi_detected": entry.get("phi_detected", False),
                            "phi_types": entry.get("phi_types"),
                            "query_hash": entry.get("query_hash"),
                            "outcome": entry.get("outcome"),
                            "details": entry.get("details"),
                        },
                    )
        finally:
            conn.close()
    except Exception as exc:
        logging.getLogger("healthnav.audit").warning("Audit DB write skipped: %s", exc)


def log_event(
    event_type: str,
    action: str,
    patient_id: Optional[str] = None,
    role: Optional[str] = None,
    phi_detected: bool = False,
    phi_types: Optional[list] = None,
    query_hash: Optional[str] = None,
    outcome: str = "success",
    details: Optional[str] = None,
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "patient_hash": _hash_value(patient_id),
        "role": role or "unknown",
        "action": action,
        "phi_detected": phi_detected,
        "phi_types": ",".join(phi_types) if phi_types else None,
        "query_hash": query_hash,
        "outcome": outcome,
        "details": details,
    }
    logger = _get_audit_logger()
    logger.info(json.dumps(entry))
    _write_to_db(entry)


def log_chat_query(patient_id: str, role: str, question_hash: str, phi_detected: bool, phi_types: list,
                   blocked: bool, sources_used: dict, source_ip: str | None = None) -> None:
    details = json.dumps({"sources": sources_used, "source_ip": source_ip}, sort_keys=True)
    log_event(
        event_type="chat_query",
        action="ask_healthnav",
        patient_id=patient_id,
        role=role,
        phi_detected=phi_detected,
        phi_types=phi_types,
        query_hash=question_hash,
        outcome="blocked" if blocked else "success",
        details=details,
    )


def log_record_access(patient_id: str, role: str, record_types: list, source_ip: str | None = None) -> None:
    details = json.dumps({"record_types": record_types, "source_ip": source_ip}, sort_keys=True)
    log_event(
        event_type="record_access",
        action="read_patient_records",
        patient_id=patient_id,
        role=role,
        details=details,
    )


def log_auth_event(username: str, outcome: str, details: str = "") -> None:
    # Hash username before logging — never store raw usernames in audit trail (HIPAA)
    import hashlib as _hl
    log_event(
        event_type="authentication",
        action="login_attempt",
        patient_id=_hl.sha256(username.encode("utf-8")).hexdigest()[:16],
        role="user",
        outcome=outcome,
        details=details,
    )
