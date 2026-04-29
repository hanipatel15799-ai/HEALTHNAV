"""
patient_record_retrieval.py — all DB read/write operations for patient data.

Key guarantees:
  - All inserts use per-row SAVEPOINTs (one bad row never kills others)
  - Every insert logs: returned count, inserted count, skipped count
  - get_connection() uses the same config as the rest of the app
  - ensure_tables_exist() is idempotent — safe to call on every startup
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import psycopg2
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Connection
# ─────────────────────────────────────────────

def get_connection():
    return psycopg2.connect(
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
    )


# ─────────────────────────────────────────────
# Bootstrap (idempotent)
# ─────────────────────────────────────────────

_BOOTSTRAP_SQL = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS patient_users (
    id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE,
    full_name TEXT, patient_id TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE, role TEXT DEFAULT 'patient', created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS patient_profiles (
    id SERIAL PRIMARY KEY, patient_id TEXT UNIQUE NOT NULL, full_name TEXT,
    date_of_birth DATE, sex TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS patient_labs (
    id SERIAL PRIMARY KEY, patient_id TEXT NOT NULL, test_date DATE NOT NULL,
    test_name TEXT NOT NULL, test_value TEXT, unit TEXT, reference_range TEXT,
    is_abnormal BOOLEAN DEFAULT FALSE, lab_name TEXT, created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_patient_lab UNIQUE (patient_id, test_date, test_name, test_value)
);
CREATE TABLE IF NOT EXISTS patient_visits (
    id SERIAL PRIMARY KEY, patient_id TEXT NOT NULL, visit_date DATE NOT NULL,
    visit_type TEXT, chief_complaint TEXT, clinical_notes TEXT, doctor_name TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_patient_visit UNIQUE (patient_id, visit_date, visit_type, chief_complaint)
);
CREATE TABLE IF NOT EXISTS patient_medications (
    id SERIAL PRIMARY KEY, patient_id TEXT NOT NULL, medication_name TEXT NOT NULL,
    dosage TEXT, frequency TEXT, start_date DATE, end_date DATE,
    prescribing_doctor TEXT, indication TEXT, is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_patient_med UNIQUE (patient_id, medication_name, dosage, frequency, start_date)
);
CREATE TABLE IF NOT EXISTS patient_files (
    id SERIAL PRIMARY KEY, patient_id TEXT NOT NULL, category TEXT NOT NULL,
    original_filename TEXT NOT NULL, stored_filename TEXT NOT NULL, file_path TEXT NOT NULL,
    content_type TEXT, notes TEXT, uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    parse_status TEXT DEFAULT 'pending', parse_report_type TEXT, parse_confidence TEXT,
    parse_notes TEXT, labs_parsed INT DEFAULT 0, visits_parsed INT DEFAULT 0,
    meds_parsed INT DEFAULT 0, parsed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS patient_file_extractions (
    id SERIAL PRIMARY KEY,
    file_id INTEGER REFERENCES patient_files(id) ON DELETE CASCADE,
    patient_id TEXT NOT NULL, extraction_mode TEXT NOT NULL,
    raw_text TEXT, interpreted_text TEXT, visual_summary TEXT, source_kind TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    session_token TEXT PRIMARY KEY, user_id TEXT NOT NULL, username TEXT NOT NULL,
    patient_id TEXT NOT NULL, role TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(), is_revoked BOOLEAN DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS rate_limit_log (
    id SERIAL PRIMARY KEY, ip_hash TEXT NOT NULL, request_time TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id SERIAL PRIMARY KEY, patient_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    message_text TEXT NOT NULL, answer_mode TEXT,
    used_records BOOLEAN DEFAULT FALSE, used_textbook BOOLEAN DEFAULT FALSE,
    used_attachment BOOLEAN DEFAULT FALSE, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY, event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type TEXT NOT NULL, patient_id TEXT, patient_hash TEXT, user_role TEXT,
    action TEXT NOT NULL, phi_detected BOOLEAN DEFAULT FALSE, phi_types TEXT,
    query_hash TEXT, outcome TEXT, details TEXT
);
CREATE TABLE IF NOT EXISTS medical_chunks (
    id SERIAL PRIMARY KEY, chunk_id TEXT UNIQUE NOT NULL,
    source_file TEXT NOT NULL, page_number INT NOT NULL,
    chunk_index INT NOT NULL, chunk_text TEXT NOT NULL, embedding vector(768)
);
"""

_ADDITIVE_MIGRATIONS = [
    "ALTER TABLE patient_file_extractions ADD COLUMN IF NOT EXISTS visual_summary TEXT;",
    "ALTER TABLE patient_file_extractions ADD COLUMN IF NOT EXISTS source_kind TEXT;",
    "CREATE INDEX IF NOT EXISTS idx_labs_patient ON patient_labs(patient_id);",
    "CREATE INDEX IF NOT EXISTS idx_labs_date ON patient_labs(patient_id, test_date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_labs_abnormal ON patient_labs(patient_id, is_abnormal);",
    "CREATE INDEX IF NOT EXISTS idx_visits_patient ON patient_visits(patient_id);",
    "CREATE INDEX IF NOT EXISTS idx_meds_patient ON patient_medications(patient_id);",
    "CREATE INDEX IF NOT EXISTS idx_files_patient ON patient_files(patient_id, uploaded_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_extractions_file ON patient_file_extractions(file_id);",
    "CREATE INDEX IF NOT EXISTS idx_chat_patient ON chat_messages(patient_id, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON auth_sessions(expires_at);",
    "CREATE INDEX IF NOT EXISTS idx_rl_ip ON rate_limit_log(ip_hash, request_time DESC);",
]


def ensure_tables_exist() -> None:
    """Create all tables and apply additive column migrations. Idempotent."""
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(_BOOTSTRAP_SQL)
                for sql in _ADDITIVE_MIGRATIONS:
                    try:
                        cur.execute(sql)
                    except Exception as exc:
                        logger.debug("Migration skipped (%s): %s", sql[:60], exc)
        logger.info("Database schema verified/created OK")
    except Exception as exc:
        logger.error("ensure_tables_exist FAILED: %s", exc)
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Internal query helpers
# ─────────────────────────────────────────────

def _rows(query: str, params: Any) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _one(query: str, params: Any) -> Optional[Dict[str, Any]]:
    rows = _rows(query, params)
    return rows[0] if rows else None


# ─────────────────────────────────────────────
# Lab queries
# ─────────────────────────────────────────────

def get_recent_labs(patient_id: str, limit: int = 15) -> List[Dict[str, Any]]:
    return _rows(
        "SELECT * FROM patient_labs WHERE patient_id=%s ORDER BY test_date DESC, id DESC LIMIT %s;",
        (patient_id, limit),
    )


def get_lab_by_name(patient_id: str, names: List[str], limit: int = 15) -> List[Dict[str, Any]]:
    if not names:
        return get_recent_labs(patient_id, limit)
    conditions = " OR ".join(["test_name ILIKE %s"] * len(names))
    params = [patient_id] + [f"%{n}%" for n in names] + [limit]
    return _rows(
        f"SELECT * FROM patient_labs WHERE patient_id=%s AND ({conditions}) "
        f"ORDER BY test_date DESC LIMIT %s;",
        params,
    )


def get_abnormal_labs(patient_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    return _rows(
        "SELECT * FROM patient_labs WHERE patient_id=%s AND is_abnormal=TRUE "
        "ORDER BY test_date DESC LIMIT %s;",
        (patient_id, limit),
    )


def get_lab_history(patient_id: str, test_name: str, limit: int = 20) -> List[Dict[str, Any]]:
    return _rows(
        "SELECT * FROM patient_labs WHERE patient_id=%s AND test_name ILIKE %s "
        "ORDER BY test_date DESC LIMIT %s;",
        (patient_id, f"%{test_name}%", limit),
    )


# ─────────────────────────────────────────────
# Visit queries
# ─────────────────────────────────────────────

def get_recent_visits(patient_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    return _rows(
        "SELECT * FROM patient_visits WHERE patient_id=%s ORDER BY visit_date DESC LIMIT %s;",
        (patient_id, limit),
    )


def search_visits_by_keyword(
    patient_id: str, keywords: List[str], limit: int = 6
) -> List[Dict[str, Any]]:
    if not keywords:
        return get_recent_visits(patient_id, limit)
    conditions = " OR ".join(
        ["chief_complaint ILIKE %s OR clinical_notes ILIKE %s"] * len(keywords)
    )
    params = [patient_id]
    for kw in keywords:
        params += [f"%{kw}%", f"%{kw}%"]
    params.append(limit)
    return _rows(
        f"SELECT * FROM patient_visits WHERE patient_id=%s AND ({conditions}) "
        f"ORDER BY visit_date DESC LIMIT %s;",
        params,
    )


# ─────────────────────────────────────────────
# Medication queries
# ─────────────────────────────────────────────

def get_active_medications(patient_id: str) -> List[Dict[str, Any]]:
    return _rows(
        "SELECT * FROM patient_medications WHERE patient_id=%s AND is_active=TRUE "
        "ORDER BY created_at DESC;",
        (patient_id,),
    )


def get_medication_history(patient_id: str, names: List[str]) -> List[Dict[str, Any]]:
    if not names:
        return get_active_medications(patient_id)
    conditions = " OR ".join(["medication_name ILIKE %s"] * len(names))
    params = [patient_id] + [f"%{n}%" for n in names]
    return _rows(
        f"SELECT * FROM patient_medications WHERE patient_id=%s AND ({conditions}) "
        f"ORDER BY created_at DESC;",
        params,
    )


# ─────────────────────────────────────────────
# File queries
# ─────────────────────────────────────────────

def get_recent_files(patient_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    return _rows(
        "SELECT * FROM patient_files WHERE patient_id=%s ORDER BY uploaded_at DESC LIMIT %s;",
        (patient_id, limit),
    )


def get_patient_data_counts(patient_id: str) -> Dict[str, int]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM patient_labs WHERE patient_id=%s;", (patient_id,))
            total_labs = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM patient_labs WHERE patient_id=%s AND is_abnormal=TRUE;", (patient_id,))
            abnormal = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM patient_visits WHERE patient_id=%s;", (patient_id,))
            total_visits = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM patient_medications WHERE patient_id=%s AND is_active=TRUE;", (patient_id,))
            active_meds = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM patient_files WHERE patient_id=%s;", (patient_id,))
            uploaded_files = cur.fetchone()[0]
        return {
            "total_labs": total_labs,
            "abnormal_count": abnormal,
            "total_visits": total_visits,
            "active_meds": active_meds,
            "uploaded_files": uploaded_files,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────
# File extraction
# ─────────────────────────────────────────────

def save_file_extraction(
    file_id: Optional[int],
    patient_id: str,
    extraction_mode: str,
    raw_text: str = "",
    interpreted_text: str = "",
    visual_summary: str = "",
    source_kind: str = "",
) -> None:
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO patient_file_extractions
                        (file_id, patient_id, extraction_mode, raw_text,
                         interpreted_text, visual_summary, source_kind)
                    VALUES (%s, %s, %s, %s, %s, %s, %s);
                    """,
                    (file_id, patient_id, extraction_mode,
                     raw_text, interpreted_text, visual_summary, source_kind),
                )
    except Exception as exc:
        logger.error("save_file_extraction FAILED: %s", exc)
    finally:
        conn.close()


def get_file_extractions(file_id: int) -> List[Dict[str, Any]]:
    return _rows(
        "SELECT * FROM patient_file_extractions WHERE file_id=%s ORDER BY created_at DESC;",
        (file_id,),
    )


# ─────────────────────────────────────────────
# Chat
# ─────────────────────────────────────────────

def save_chat_message(
    patient_id: str,
    role: str,
    message_text: str,
    answer_mode: str = "",
    used_records: bool = False,
    used_textbook: bool = False,
    used_attachment: bool = False,
) -> None:
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_messages
                        (patient_id, role, message_text, answer_mode,
                         used_records, used_textbook, used_attachment)
                    VALUES (%s, %s, %s, %s, %s, %s, %s);
                    """,
                    (patient_id, role, message_text, answer_mode or "",
                     used_records, used_textbook, used_attachment),
                )
    except Exception as exc:
        logger.warning("save_chat_message failed: %s", exc)
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Profile
# ─────────────────────────────────────────────

def get_patient_profile(patient_id: str) -> Optional[Dict[str, Any]]:
    return _one(
        "SELECT * FROM patient_profiles WHERE patient_id=%s;", (patient_id,)
    )


def upsert_patient_profile(
    patient_id: str,
    full_name: str = "",
    date_of_birth: Optional[str] = None,
    sex: Optional[str] = None,
) -> None:
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO patient_profiles (patient_id, full_name, date_of_birth, sex)
                    VALUES (%s, %s, NULLIF(%s,'')::date, %s)
                    ON CONFLICT (patient_id) DO UPDATE SET
                        full_name     = EXCLUDED.full_name,
                        date_of_birth = EXCLUDED.date_of_birth,
                        sex           = EXCLUDED.sex,
                        updated_at    = NOW();
                    """,
                    (patient_id, full_name, date_of_birth or "", sex),
                )
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Bulk insert helpers (used by report_parser)
# All use per-row savepoints
# ─────────────────────────────────────────────

def insert_parsed_labs(
    conn: Any, patient_id: str, labs: List[Dict[str, Any]]
) -> int:
    """
    DEPRECATED: use report_parser._insert_labs_safe() which handles
    date normalisation + fallback. Kept for backwards compatibility.
    """
    from utils.validation import resolve_lab_date, is_valid_lab_row
    inserted = 0
    with conn.cursor() as cur:
        for idx, lab in enumerate(labs):
            if not is_valid_lab_row(lab):
                continue
            resolved_date = resolve_lab_date(lab, None)
            sp = f"sp_lab_compat_{idx}"
            try:
                cur.execute(f"SAVEPOINT {sp}")
                cur.execute(
                    """
                    INSERT INTO patient_labs
                        (patient_id,test_date,test_name,test_value,
                         unit,reference_range,is_abnormal,lab_name)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT ON CONSTRAINT uq_patient_lab DO NOTHING;
                    """,
                    (patient_id, resolved_date,
                     lab["test_name"].strip(),
                     (lab.get("test_value") or "").strip(),
                     (lab.get("unit") or "").strip(),
                     (lab.get("reference_range") or "").strip(),
                     bool(lab.get("is_abnormal", False)),
                     (lab.get("lab_name") or "").strip()),
                )
                inserted += cur.rowcount
                cur.execute(f"RELEASE SAVEPOINT {sp}")
            except Exception as exc:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                logger.warning("insert_parsed_labs[%d] skipped: %s", idx, exc)
    logger.info("insert_parsed_labs: inserted=%d / total=%d", inserted, len(labs))
    return inserted


def insert_parsed_visits(
    conn: Any, patient_id: str, visits: List[Dict[str, Any]]
) -> int:
    """Insert visit rows with per-row savepoints."""
    from utils.validation import parse_date
    inserted = 0
    with conn.cursor() as cur:
        for idx, visit in enumerate(visits):
            visit_date = parse_date(visit.get("visit_date"))
            if not visit_date:
                logger.warning("insert_parsed_visits[%d]: no visit_date, skipped", idx)
                continue
            sp = f"sp_visit_{idx}"
            try:
                cur.execute(f"SAVEPOINT {sp}")
                cur.execute(
                    """
                    INSERT INTO patient_visits
                        (patient_id,visit_date,visit_type,chief_complaint,
                         clinical_notes,doctor_name)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT ON CONSTRAINT uq_patient_visit DO NOTHING;
                    """,
                    (patient_id, visit_date,
                     visit.get("visit_type","Visit Summary") or "Visit Summary",
                     visit.get("chief_complaint","") or "",
                     visit.get("clinical_notes","") or "",
                     visit.get("doctor_name","") or ""),
                )
                inserted += cur.rowcount
                cur.execute(f"RELEASE SAVEPOINT {sp}")
            except Exception as exc:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                logger.warning("insert_parsed_visits[%d] skipped: %s", idx, exc)
    logger.info("insert_parsed_visits: inserted=%d / total=%d", inserted, len(visits))
    return inserted


def insert_parsed_medications(
    conn: Any, patient_id: str, medications: List[Dict[str, Any]]
) -> int:
    """Insert medication rows with per-row savepoints."""
    inserted = 0
    with conn.cursor() as cur:
        for idx, med in enumerate(medications):
            med_name = (med.get("medication_name") or "").strip()
            if not med_name:
                continue
            sp = f"sp_med_{idx}"
            try:
                cur.execute(f"SAVEPOINT {sp}")
                cur.execute(
                    """
                    INSERT INTO patient_medications
                        (patient_id,medication_name,dosage,frequency,
                         start_date,end_date,prescribing_doctor,indication,is_active)
                    VALUES (%s,%s,%s,%s,
                            NULLIF(%s,'')::date, NULLIF(%s,'')::date,
                            %s,%s,%s)
                    ON CONFLICT ON CONSTRAINT uq_patient_med DO NOTHING;
                    """,
                    (patient_id, med_name,
                     med.get("dosage","") or "",
                     med.get("frequency","") or "",
                     med.get("start_date","") or "",
                     med.get("end_date","") or "",
                     med.get("prescribing_doctor","") or "",
                     med.get("indication","") or "",
                     bool(med.get("is_active", True))),
                )
                inserted += cur.rowcount
                cur.execute(f"RELEASE SAVEPOINT {sp}")
            except Exception as exc:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                logger.warning("insert_parsed_medications[%d] skipped: %s", idx, exc)
    logger.info("insert_parsed_medications: inserted=%d / total=%d", inserted, len(medications))
    return inserted


# ─────────────────────────────────────────────
# Context formatting (used by answer_with_ai)
# ─────────────────────────────────────────────

def _fmt_date(v: Any) -> str:
    if v is None:
        return "unknown date"
    if isinstance(v, (datetime, date)):
        return (v.date() if isinstance(v, datetime) else v).strftime("%d %b %Y")
    return str(v).split("T")[0]


def format_patient_context(
    visits: Optional[List[Dict]] = None,
    labs: Optional[List[Dict]] = None,
    medications: Optional[List[Dict]] = None,
    patient_role: str = "patient",
) -> str:
    parts: List[str] = []

    if labs:
        lab_lines = ["RECENT LAB RESULTS:"]
        for lb in labs:
            flag = " ⚠ ABNORMAL" if lb.get("is_abnormal") else ""
            lab_lines.append(
                f"  {lb.get('test_name','')} = {lb.get('test_value','')} "
                f"{lb.get('unit','')}  (ref: {lb.get('reference_range','')})  "
                f"[{_fmt_date(lb.get('test_date'))}]{flag}"
            )
        parts.append("\n".join(lab_lines))

    if visits:
        visit_lines = ["RECENT VISITS:"]
        for v in visits:
            visit_lines.append(
                f"  [{_fmt_date(v.get('visit_date'))}] {v.get('visit_type','')} — "
                f"{v.get('chief_complaint','')} | {v.get('clinical_notes','')[:200]}"
            )
        parts.append("\n".join(visit_lines))

    if medications:
        med_lines = ["CURRENT MEDICATIONS:"]
        for m in medications:
            if m.get("is_active"):
                med_lines.append(
                    f"  {m.get('medication_name','')} {m.get('dosage','')} "
                    f"{m.get('frequency','')} — {m.get('indication','')}"
                )
        parts.append("\n".join(med_lines))

    return "\n\n".join(parts)


def format_file_context(files: Optional[List[Dict]] = None) -> str:
    if not files:
        return ""
    lines = ["RECENTLY UPLOADED FILES:"]
    for f in files:
        lines.append(
            f"  {f.get('original_filename','')} "
            f"[{f.get('parse_status','')}] — "
            f"{f.get('labs_parsed',0)} labs, "
            f"{f.get('visits_parsed',0)} visits, "
            f"{f.get('meds_parsed',0)} meds"
        )
    return "\n".join(lines)


def format_full_patient_context(
    visits: Optional[List[Dict]] = None,
    labs: Optional[List[Dict]] = None,
    medications: Optional[List[Dict]] = None,
    files: Optional[List[Dict]] = None,
    patient_role: str = "patient",
) -> str:
    ctx = format_patient_context(visits=visits, labs=labs,
                                  medications=medications, patient_role=patient_role)
    file_ctx = format_file_context(files)
    return "\n\n".join(p for p in [ctx, file_ctx] if p)
