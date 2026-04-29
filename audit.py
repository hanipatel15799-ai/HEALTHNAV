"""
audit.py — unified audit logging for HealthNav.

Provides both APIs consumed by the codebase:
  write_audit_log()   ← main.py
  log_chat_query()    ← answer_with_ai.py
  log_record_access() ← answer_with_ai.py
  log_auth_event()    ← auth.py

Storage:
  DB:   audit_log table (raw patient_id for admin queries)
  File: audit/audit.log (patient_hash only — never raw PII)

Both writes are non-fatal.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_AUDIT_DIR = Path(os.getenv("AUDIT_LOG_DIR", "audit"))
_AUDIT_FILE = _AUDIT_DIR / "audit.log"
_MAX_BYTES = int(os.getenv("AUDIT_MAX_BYTES", str(10 * 1024 * 1024)))
_BACKUP_COUNT = int(os.getenv("AUDIT_BACKUP_COUNT", "10"))
_file_logger: Optional[logging.Logger] = None


def _get_file_logger() -> logging.Logger:
    global _file_logger
    if _file_logger is not None:
        return _file_logger
    _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger("healthnav.audit")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    if not lg.handlers:
        h = RotatingFileHandler(_AUDIT_FILE, maxBytes=_MAX_BYTES,
                                backupCount=_BACKUP_COUNT, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(message)s"))
        lg.addHandler(h)
    _file_logger = lg
    return lg


def _hash(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    return hashlib.sha256(v.encode()).hexdigest()[:16]


def _to_db(entry: Dict[str, Any]) -> None:
    try:
        from db.db import get_connection
        conn = get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO audit_log
                            (event_time, event_type, patient_id, patient_hash,
                             user_role, action, phi_detected, phi_types,
                             query_hash, outcome, details)
                        VALUES (%(ts)s,%(et)s,%(pid)s,%(ph)s,%(role)s,%(action)s,
                                %(phi)s,%(ptypes)s,%(qh)s,%(outcome)s,%(details)s);
                    """, {
                        "ts": entry["timestamp"], "et": entry["event_type"],
                        "pid": entry.get("patient_id"), "ph": entry.get("patient_hash"),
                        "role": entry.get("role"), "action": entry["action"],
                        "phi": entry.get("phi_detected", False),
                        "ptypes": entry.get("phi_types"),
                        "qh": entry.get("query_hash"),
                        "outcome": entry.get("outcome"),
                        "details": entry.get("details"),
                    })
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("Audit DB write skipped: %s", exc)


def log_event(
    event_type: str,
    action: str,
    patient_id: Optional[str] = None,
    role: Optional[str] = None,
    phi_detected: bool = False,
    phi_types: Optional[List[str]] = None,
    query_hash: Optional[str] = None,
    outcome: str = "success",
    details: Optional[str] = None,
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "patient_id": patient_id,
        "patient_hash": _hash(patient_id),
        "role": role or "unknown",
        "action": action,
        "phi_detected": phi_detected,
        "phi_types": ",".join(phi_types) if phi_types else None,
        "query_hash": query_hash,
        "outcome": outcome,
        "details": details,
    }
    # File log — use hash only
    file_entry = {**entry, "patient_id": None, "patient_hash": entry["patient_hash"]}
    _get_file_logger().info(json.dumps(file_entry))
    _to_db(entry)


def write_audit_log(
    event_type: str,
    action: str,
    patient_id: Optional[str] = None,
    **kwargs,
) -> None:
    log_event(event_type=event_type, action=action, patient_id=patient_id, **kwargs)


def log_chat_query(
    patient_id: str, role: str, question_hash: str,
    phi_detected: bool, phi_types: List[str],
    blocked: bool, sources_used: Dict, source_ip: Optional[str] = None,
) -> None:
    details = json.dumps({"sources": sources_used, "source_ip": source_ip}, sort_keys=True)
    log_event(
        event_type="chat_query", action="ask_healthnav",
        patient_id=patient_id, role=role,
        phi_detected=phi_detected, phi_types=phi_types,
        query_hash=question_hash,
        outcome="blocked" if blocked else "success",
        details=details,
    )


def log_record_access(
    patient_id: str, role: str, record_types: List[str],
    source_ip: Optional[str] = None,
) -> None:
    details = json.dumps({"record_types": record_types, "source_ip": source_ip}, sort_keys=True)
    log_event(
        event_type="record_access", action="read_patient_records",
        patient_id=patient_id, role=role, details=details,
    )


def log_auth_event(username: str, outcome: str, details: str = "") -> None:
    log_event(
        event_type="authentication", action="login_attempt",
        patient_id=hashlib.sha256(username.encode()).hexdigest()[:16],
        role="user", outcome=outcome, details=details,
    )
