"""
main.py — HealthNav FastAPI (Production / AWS) v2.2.0

Pipeline: UI → FastAPI → S3 → DB → Parser → AI

All fixes:
  FIX 1 — CORS: no hardcoded localhost (use CORS_EXTRA_ORIGINS in local .env)
  FIX 2 — S3 orphan: delete_from_s3() if DB insert fails after upload
  FIX 3 — Re-parse: fetches + passes category from DB correctly
  FIX 4 — Register: atomic INSERT ON CONFLICT, clean 409
  FIX 5 — Login: accepts username OR email
  FIX 6 — Chat history: GET /api/chat/history per patient (persistent)
  FIX 7 — No local file storage anywhere
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    BackgroundTasks, FastAPI, File, Form,
    HTTPException, Request, Response, UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from answer_with_ai import answer_question
from api_models import ChatRequest, ChatResponse, HealthResponse
from auth import (
    cleanup_expired_sessions, create_demo_user_if_missing,
    create_session_token, ensure_auth_support_tables,
    get_current_session, hash_password,
    invalidate_session_token, verify_user_credentials,
)
from config import get_app_config, missing_core_env
from patient_record_retrieval import (
    ensure_tables_exist, get_abnormal_labs, get_active_medications,
    get_connection, get_patient_data_counts, get_recent_files,
    get_recent_labs, get_recent_visits,
)
from s3_client import (
    upload_bytes_to_s3, download_bytes_from_s3,
    generate_presigned_url, delete_from_s3,
)

app_config = get_app_config()
logging.basicConfig(level=app_config.log_level, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MAX_QUESTION_LEN       = int(os.getenv("MAX_QUESTION_LEN", "1000"))
RATE_LIMIT_REQUESTS    = int(os.getenv("RATE_LIMIT_REQUESTS", "20"))
RATE_LIMIT_WINDOW_SECS = int(os.getenv("RATE_LIMIT_WINDOW_SECS", "60"))
SESSION_TTL_SECS       = int(os.getenv("SESSION_TTL_SECS", "604800"))
COOKIE_SECURE          = os.getenv("COOKIE_SECURE", "false").lower() == "true"
MAX_UPLOAD_BYTES       = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))

PARSEABLE_TYPES = frozenset({
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".bmp", ".tif", ".tiff", ".txt", ".csv",
})
ALLOWED_CATEGORIES = frozenset({
    "visit_summary", "discharge_summary", "lab_report",
    "medication_list", "chat_attachment", "other",
})


# ── Startup ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = missing_core_env()
    if missing:
        logger.warning("Missing env vars: %s", missing)
    try:
        ensure_tables_exist()
        ensure_auth_support_tables()
        create_demo_user_if_missing()
        cleanup_expired_sessions()
        logger.info("HealthNav startup complete.")
    except Exception as exc:
        logger.error("Startup DB init failed: %s", exc)
    yield


app = FastAPI(title="HealthNav", version="2.2.0", lifespan=lifespan)

# FIX 1: No hardcoded localhost in production CORS
_extra = [o.strip() for o in os.getenv("CORS_EXTRA_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=[app_config.frontend_origin] + _extra,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (
        request.client.host if request.client else "unknown"
    )


def _session_or_401(request: Request) -> dict:
    session = get_current_session(request.cookies.get("session_token"))
    if not session:
        raise HTTPException(401, "Not authenticated.")
    return session


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="session_token", value=token, httponly=True,
        secure=COOKIE_SECURE, samesite="lax", max_age=SESSION_TTL_SECS,
    )


def _rate_limit(ip: str) -> bool:
    import hashlib
    ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:32]
    try:
        conn = get_connection()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM rate_limit_log WHERE ip_hash=%s"
                        " AND request_time > NOW() - (%s * INTERVAL '1 second');",
                        (ip_hash, RATE_LIMIT_WINDOW_SECS),
                    )
                    if cur.fetchone()[0] >= RATE_LIMIT_REQUESTS:
                        return False
                    cur.execute(
                        "INSERT INTO rate_limit_log (ip_hash) VALUES (%s);",
                        (ip_hash,)
                    )
                    cur.execute(
                        "DELETE FROM rate_limit_log WHERE ip_hash=%s"
                        " AND request_time < NOW() - (%s * INTERVAL '1 second');",
                        (ip_hash, RATE_LIMIT_WINDOW_SECS * 2),
                    )
            return True
        finally:
            conn.close()
    except Exception:
        return True


def _run_parse_background(
    patient_id: str, s3_key: str, content_type: str,
    raw_bytes: bytes, file_id: int, category: str = "other",
) -> None:
    """Background parse — bytes in memory, never touches local disk."""
    from report_parser import parse_and_store
    try:
        fake_path = Path(f"s3://{s3_key}")
        parse_and_store(
            patient_id, fake_path, content_type,
            raw_bytes, file_id, category=category,
        )
    except Exception as exc:
        logger.exception("Background parse FAILED file_id=%s: %s", file_id, exc)
        _mark_file_failed(file_id, f"crash: {str(exc)[:300]}")


def _mark_file_failed(file_id: int, reason: str) -> None:
    try:
        conn = get_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE patient_files SET parse_status='failed',"
                    " parse_notes=%s, parsed_at=NOW() WHERE id=%s;",
                    (reason[:300], file_id),
                )
        conn.close()
    except Exception as e:
        logger.error("Could not mark file as failed: %s", e)


def _save_chat_message(patient_id: str, role: str, text: str) -> None:
    """Persist a chat message so users see their history on next login."""
    try:
        conn = get_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO chat_messages (patient_id, role, message_text)"
                    " VALUES (%s, %s, %s);",
                    (patient_id, role, text[:4000]),
                )
        conn.close()
    except Exception as exc:
        logger.warning("chat_messages insert skipped (non-fatal): %s", exc)


# ── Static files ──────────────────────────────────────────────────────────────

if Path("static").exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def serve_index():
    if Path("static/index.html").exists():
        return FileResponse("static/index.html")
    return {"message": "HealthNav API v2.2 — /docs"}


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse)
async def health():
    db_ok, ai_ok, s3_ok = "ok", "ok", "ok"
    try:
        get_connection().close()
    except Exception as e:
        db_ok = f"error: {e}"
    try:
        from vertex_client import get_vertex_client
        get_vertex_client()
    except Exception as e:
        ai_ok = f"error: {e}"
    try:
        from s3_client import get_s3_client, _bucket
        get_s3_client().head_bucket(Bucket=_bucket())
    except Exception as e:
        s3_ok = f"error: {e}"
    return HealthResponse(api="ok", db=db_ok, vertex=ai_ok, details=f"s3={s3_ok}")


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/api/register")
async def register(request: Request, response: Response):
    body      = await request.json()
    username  = (body.get("username") or "").strip()
    email     = (body.get("email") or "").strip().lower()
    full_name = (body.get("full_name") or "").strip()
    password  = body.get("password") or ""
    confirm   = body.get("confirm_password") or ""

    if not username or not password:
        raise HTTPException(400, "Username and password required.")
    if len(username) < 3:
        raise HTTPException(400, "Username must be at least 3 characters.")
    if not email or "@" not in email:
        raise HTTPException(400, "Valid email is required.")
    if password != confirm:
        raise HTTPException(400, "Passwords do not match.")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    patient_id = f"p-{uuid.uuid4().hex[:12]}"
    pw_hash    = hash_password(password)

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                # FIX 4: Atomic INSERT — eliminates race condition
                cur.execute(
                    """INSERT INTO patient_users
                        (username, email, full_name, patient_id, password_hash, role)
                       VALUES (%s,%s,%s,%s,%s,'patient')
                       ON CONFLICT (username) DO NOTHING
                       RETURNING id;""",
                    (username, email, full_name, patient_id, pw_hash),
                )
                if not cur.fetchone():
                    cur.execute(
                        "SELECT 1 FROM patient_users WHERE email=%s;", (email,)
                    )
                    msg = ("Email already registered."
                           if cur.fetchone() else "Username already taken.")
                    raise HTTPException(409, msg)
                cur.execute(
                    "INSERT INTO patient_profiles (patient_id, full_name)"
                    " VALUES (%s,%s) ON CONFLICT (patient_id) DO NOTHING;",
                    (patient_id, full_name),
                )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("register failed: %s", exc)
        raise HTTPException(500, "Registration failed. Please try again.")
    finally:
        conn.close()

    token = create_session_token(
        user_id=patient_id, username=username,
        patient_id=patient_id, role="patient",
    )
    _set_cookie(response, token)
    return {"ok": True, "username": username,
            "patient_id": patient_id, "role": "patient"}


@app.post("/api/login")
async def login(request: Request, response: Response):
    body = await request.json()
    # FIX 5: accept username OR email
    login_id = (body.get("username") or body.get("email") or "").strip()
    password  = body.get("password") or ""

    if not login_id or not password:
        raise HTTPException(400, "Username/email and password required.")

    user = verify_user_credentials(login_id, password)
    if not user:
        raise HTTPException(401, "Invalid credentials.")

    token = create_session_token(
        user_id=user["user_id"],
        username=user["username"],
        patient_id=user["patient_id"],
        role=user.get("role", "patient"),
    )
    _set_cookie(response, token)
    return {
        "ok": True,
        "patient_id": user["patient_id"],
        "username": user["username"],
        "role": user.get("role", "patient"),
    }


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("session_token")
    if token:
        try:
            invalidate_session_token(token)
        except Exception:
            pass
    response.delete_cookie("session_token")
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request):
    s = _session_or_401(request)
    return {"ok": True, "username": s["username"],
            "patient_id": s["patient_id"], "role": s["role"]}


# ── Profile ───────────────────────────────────────────────────────────────────

@app.get("/api/profile")
async def get_profile(request: Request):
    s = _session_or_401(request)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT patient_id,full_name,date_of_birth,sex"
                " FROM patient_profiles WHERE patient_id=%s;",
                (s["patient_id"],),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return {"patient_id": s["patient_id"], "full_name": "",
                "date_of_birth": None, "sex": None}
    return {"patient_id": row[0], "full_name": row[1] or "",
            "date_of_birth": row[2].isoformat() if row[2] else None,
            "sex": row[3]}


@app.patch("/api/profile")
async def update_profile(request: Request):
    s    = _session_or_401(request)
    body = await request.json()
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO patient_profiles
                        (patient_id,full_name,date_of_birth,sex,updated_at)
                       VALUES (%s,%s,%s,%s,NOW())
                       ON CONFLICT (patient_id) DO UPDATE SET
                           full_name=EXCLUDED.full_name,
                           date_of_birth=EXCLUDED.date_of_birth,
                           sex=EXCLUDED.sex, updated_at=NOW();""",
                    (s["patient_id"],
                     (body.get("full_name") or "").strip(),
                     body.get("date_of_birth") or None,
                     body.get("sex") or None),
                )
    finally:
        conn.close()
    return {"ok": True}


# ── Labs ──────────────────────────────────────────────────────────────────────

@app.get("/api/labs")
async def labs(request: Request):
    s = _session_or_401(request)
    return {"labs": get_recent_labs(s["patient_id"], limit=50)}


@app.get("/api/labs/abnormal")
async def abnormal_labs(request: Request):
    s = _session_or_401(request)
    return {"labs": get_abnormal_labs(s["patient_id"], limit=20)}


@app.post("/api/labs/add")
async def add_lab(request: Request):
    s = _session_or_401(request)
    b = await request.json()
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO patient_labs
                        (patient_id,test_date,test_name,test_value,unit,
                         reference_range,is_abnormal,lab_name)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT ON CONSTRAINT uq_patient_lab DO NOTHING
                       RETURNING id;""",
                    (s["patient_id"], b.get("test_date"), b.get("test_name"),
                     b.get("test_value", ""), b.get("unit", ""),
                     b.get("reference_range", ""),
                     bool(b.get("is_abnormal", False)),
                     b.get("lab_name", "")),
                )
                row = cur.fetchone()
    finally:
        conn.close()
    return {"ok": True, "id": row[0] if row else None}


# ── Visits ────────────────────────────────────────────────────────────────────

@app.get("/api/visits")
async def visits(request: Request):
    s = _session_or_401(request)
    return {"visits": get_recent_visits(s["patient_id"], limit=20)}


@app.post("/api/visits/add")
async def add_visit(request: Request):
    s = _session_or_401(request)
    b = await request.json()
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO patient_visits
                        (patient_id,visit_date,visit_type,chief_complaint,
                         clinical_notes,doctor_name)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT ON CONSTRAINT uq_patient_visit DO NOTHING
                       RETURNING id;""",
                    (s["patient_id"], b.get("visit_date"),
                     b.get("visit_type", "General"),
                     b.get("chief_complaint", ""),
                     b.get("clinical_notes", ""),
                     b.get("doctor_name", "")),
                )
                row = cur.fetchone()
    finally:
        conn.close()
    return {"ok": True, "id": row[0] if row else None}


# ── Medications ───────────────────────────────────────────────────────────────

@app.get("/api/medications")
async def medications(request: Request):
    s = _session_or_401(request)
    return {"medications": get_active_medications(s["patient_id"])}


@app.post("/api/medications/add")
async def add_medication(request: Request):
    s = _session_or_401(request)
    b = await request.json()
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO patient_medications
                        (patient_id,medication_name,dosage,frequency,start_date,
                         end_date,prescribing_doctor,indication,is_active)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT ON CONSTRAINT uq_patient_med DO NOTHING
                       RETURNING id;""",
                    (s["patient_id"], b.get("medication_name"),
                     b.get("dosage", ""), b.get("frequency", ""),
                     b.get("start_date") or None, b.get("end_date") or None,
                     b.get("prescribing_doctor", ""),
                     b.get("indication", ""),
                     bool(b.get("is_active", True))),
                )
                row = cur.fetchone()
    finally:
        conn.close()
    return {"ok": True, "id": row[0] if row else None}


@app.get("/api/summary")
async def summary(request: Request):
    s = _session_or_401(request)
    return get_patient_data_counts(s["patient_id"])


# ── Files ─────────────────────────────────────────────────────────────────────

@app.get("/api/files")
async def list_files(request: Request):
    s = _session_or_401(request)
    return {"files": get_recent_files(s["patient_id"], limit=50)}


@app.post("/api/upload")
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    category: str = Form("other"),
    notes: str = Form(""),
) -> dict:
    s = _session_or_401(request)

    if category not in ALLOWED_CATEGORIES:
        raise HTTPException(400, "Invalid category.")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(400, "File is empty.")
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"File too large. Max {MAX_UPLOAD_BYTES // (1024*1024)} MB."
        )

    original  = file.filename or "upload.bin"
    ext       = Path(original).suffix.lower()
    stored    = f"{uuid.uuid4().hex}{ext}"
    can_parse = ext in PARSEABLE_TYPES

    # Step 1: Upload to S3 — no local disk write ever
    s3_key = upload_bytes_to_s3(
        raw_bytes=raw_bytes,
        patient_id=s["patient_id"],
        stored_filename=stored,
        content_type=file.content_type or "application/octet-stream",
    )

    # Step 2: Save to DB — FIX 2: roll back S3 on failure
    conn = get_connection()
    file_id = None
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO patient_files
                        (patient_id,category,original_filename,stored_filename,
                         file_path,content_type,notes,parse_status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id;""",
                    (s["patient_id"], category, original, stored,
                     s3_key,  # S3 key in DB — never a local path
                     file.content_type or "application/octet-stream",
                     notes, "queued" if can_parse else "unsupported"),
                )
                file_id = cur.fetchone()[0]
    except Exception as exc:
        logger.error("DB insert failed — rolling back S3: %s", exc)
        delete_from_s3(s3_key)
        raise HTTPException(500, "Upload failed. Please try again.")
    finally:
        conn.close()

    # Step 3: Parse in background
    if can_parse:
        background_tasks.add_task(
            _run_parse_background,
            patient_id=s["patient_id"],
            s3_key=s3_key,
            content_type=file.content_type or "",
            raw_bytes=raw_bytes,
            file_id=file_id,
            category=category,
        )

    return {
        "ok": True, "file_id": file_id, "filename": original,
        "stored_filename": stored, "category": category,
        "can_parse": can_parse,
        "message": "Parsing started." if can_parse else "File saved.",
    }


@app.get("/api/files/{file_id}/parse-status")
async def parse_status(file_id: int, request: Request):
    s = _session_or_401(request)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT parse_status,parse_report_type,parse_confidence,
                          parse_notes,labs_parsed,visits_parsed,meds_parsed,parsed_at
                   FROM patient_files WHERE id=%s AND patient_id=%s;""",
                (file_id, s["patient_id"]),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "File not found.")
    status, rtype, conf, notes, labs, vis, meds, parsed_at = row
    return {
        "file_id": file_id, "parse_status": status,
        "report_type": rtype, "confidence": conf,
        "parse_notes": notes,
        "labs_inserted": labs or 0,
        "visits_inserted": vis or 0,
        "meds_inserted": meds or 0,
        "parsed_at": parsed_at.isoformat() if parsed_at else None,
        "done": status in ("done", "failed", "unsupported"),
    }


@app.post("/api/files/{file_id}/reparse")
async def reparse_file(
    file_id: int, request: Request, background_tasks: BackgroundTasks
):
    s = _session_or_401(request)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # FIX 3: fetch real category so re-parse uses correct schema
            cur.execute(
                "SELECT file_path,content_type,category"
                " FROM patient_files WHERE id=%s AND patient_id=%s;",
                (file_id, s["patient_id"]),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        raise HTTPException(404, "File not found.")

    s3_key, content_type, category = row

    try:
        raw_bytes = download_bytes_from_s3(s3_key)
    except Exception as exc:
        raise HTTPException(410, f"File no longer in S3: {exc}")

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE patient_files SET parse_status='queued',
                       parse_report_type=NULL,parse_confidence=NULL,
                       parse_notes=NULL,labs_parsed=0,visits_parsed=0,
                       meds_parsed=0,parsed_at=NULL WHERE id=%s;""",
                    (file_id,),
                )
    finally:
        conn.close()

    background_tasks.add_task(
        _run_parse_background,
        patient_id=s["patient_id"],
        s3_key=s3_key,
        content_type=content_type or "",
        raw_bytes=raw_bytes,
        file_id=file_id,
        category=category or "other",
    )
    return {"ok": True, "message": "Re-parse queued."}


@app.get("/api/files/{file_id}/download-url")
async def file_download_url(file_id: int, request: Request):
    """15-minute pre-signed S3 URL for the patient to download their file."""
    s = _session_or_401(request)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_path,original_filename FROM patient_files"
                " WHERE id=%s AND patient_id=%s;",
                (file_id, s["patient_id"]),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "File not found.")
    url = generate_presigned_url(row[0], expires_in=900)
    if not url:
        raise HTTPException(500, "Could not generate download URL.")
    return {"url": url, "filename": row[1], "expires_in": 900}


# ── Chat ──────────────────────────────────────────────────────────────────────

@app.get("/api/chat/history")
async def chat_history(request: Request):
    """
    FIX 6: Per-patient chat history.
    Returns last 50 messages for the logged-in patient only.
    Called by frontend on login — each user sees their own conversation,
    never another patient's.
    """
    s = _session_or_401(request)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT role, message_text, created_at
                   FROM chat_messages
                   WHERE patient_id=%s
                   ORDER BY created_at ASC
                   LIMIT 50;""",
                (s["patient_id"],),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return {
        "messages": [
            {
                "role": r[0],
                "message_text": r[1],
                "created_at": r[2].isoformat() if r[2] else None,
            }
            for r in rows
        ]
    }


@app.post("/api/chat")
async def chat(request: Request, chat_request: ChatRequest):
    s = _session_or_401(request)
    ip = _ip(request)

    if not _rate_limit(ip):
        raise HTTPException(429, "Too many requests.")

    if len(chat_request.question) > MAX_QUESTION_LEN:
        raise HTTPException(400, "Question too long.")

    result = await answer_question(
        raw_question=chat_request.question,
        patient_id=s["patient_id"],
        role=s["role"],
        source_ip=ip,
    )

    return {
        "answer": result.get("answer", "No response generated")
    }


@app.post("/api/chat-with-file")
async def chat_with_file(
    request: Request,
    background_tasks: BackgroundTasks,
    question: str = Form(...),
    save_to_record: bool = Form(False),
    file: UploadFile | None = File(None),
):
    s = _session_or_401(request)
    ip = _ip(request)
    if not _rate_limit(ip):
        raise HTTPException(429, "Too many requests.")
    if not question.strip():
        raise HTTPException(400, "Question required.")

    attachment_text  = ""
    attachment_name  = None
    attachment_saved = False

    if file is not None:
        raw_bytes = await file.read()
        if not raw_bytes:
            raise HTTPException(400, "Attached file is empty.")
        if len(raw_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File too large.")

        original     = file.filename or "upload.bin"
        content_type = file.content_type or "application/octet-stream"
        attachment_name = original
        ext = Path(original).suffix.lower()

        # Extract text in memory — no disk write
        try:
            from report_parser import extract_attachment_context_for_chat
            fake_path = Path(f"memory://{uuid.uuid4().hex}{ext}")
            attachment_text, _meta = extract_attachment_context_for_chat(
                fake_path, content_type, raw_bytes
            )
        except Exception as exc:
            logger.exception("Chat attachment extraction failed: %s", exc)

        if save_to_record:
            stored = f"{uuid.uuid4().hex}{ext}"
            s3_key = upload_bytes_to_s3(
                raw_bytes=raw_bytes,
                patient_id=s["patient_id"],
                stored_filename=stored,
                content_type=content_type,
            )
            conn = get_connection()
            fid = None
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO patient_files
                                (patient_id,category,original_filename,
                                 stored_filename,file_path,content_type,
                                 notes,parse_status)
                               VALUES (%s,'chat_attachment',%s,%s,%s,%s,
                                       'Saved from chat.',%s)
                               RETURNING id;""",
                            (s["patient_id"], original, stored,
                             s3_key, content_type,
                             "queued" if ext in PARSEABLE_TYPES else "unsupported"),
                        )
                        fid = cur.fetchone()[0]
                attachment_saved = True
            except Exception as exc:
                logger.error("chat-with-file DB failed, rolling back S3: %s", exc)
                delete_from_s3(s3_key)
            finally:
                conn.close()

            if fid and ext in PARSEABLE_TYPES:
                background_tasks.add_task(
                    _run_parse_background,
                    patient_id=s["patient_id"],
                    s3_key=s3_key,
                    content_type=content_type,
                    raw_bytes=raw_bytes,
                    file_id=fid,
                    category="chat_attachment",
                )

    result = await answer_question(
        raw_question=question,
        patient_id=s["patient_id"],
        role=s["role"],
        source_ip=ip,
        attachment_text=attachment_text,
        attachment_name=attachment_name,
    )

    # Persist chat history for file-based chats too
    _save_chat_message(s["patient_id"], "user", question)
    if result.get("answer"):
        _save_chat_message(s["patient_id"], "assistant", result["answer"])

    result["attachment_used"]  = bool(attachment_text.strip())
    result["attachment_saved"] = attachment_saved
    return result
