"""
answer_with_ai.py — HealthNav AI reasoning pipeline.

Updated for stable local development:
  - Uses ONE Vertex AI call per chat answer.
  - Removes Vertex-based intent classification and query expansion.
  - Uses fast local keyword classification to decide whether to pull labs,
    visits, medications, and files.
  - Keeps textbook retrieval, patient record retrieval, PHI guard, safety checks,
    audit logging, and chat saving.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from audit import log_chat_query, log_record_access
from normalize_query import normalize_query
from patient_record_retrieval import (
    ensure_tables_exist,
    format_full_patient_context,
    get_abnormal_labs,
    get_active_medications,
    get_lab_by_name,
    get_lab_history,
    get_medication_history,
    get_recent_files,
    get_recent_labs,
    get_recent_visits,
    save_chat_message,
    search_visits_by_keyword,
)
from phi_guard import detect_phi, redact_phi
from search_chunks import hybrid_search
from trend_analysis import analyze_all_labs, format_trend_summary
from vertex_client import get_vertex_client, get_vertex_model_name

load_dotenv()
logger = logging.getLogger(__name__)

# These sync with your .env. Defaults are safe if .env value is missing.
_VERTEX_RETRIES = int(os.getenv("VERTEX_RETRIES", "2"))
TEXTBOOK_TOP_K = int(os.getenv("TEXTBOOK_TOP_K", "1"))
MAX_CHUNK_CHARS = int(os.getenv("MAX_CHUNK_CHARS", "500"))
MAX_ATTACHMENT_CHARS = int(os.getenv("MAX_ATTACHMENT_CHARS", "3000"))
RECENT_FILES_LIMIT = int(os.getenv("RECENT_FILES_LIMIT", "5"))

SAFETY_DISCLAIMER = (
    "\n\nPlease use this only as a guide and verify any interpretation, "
    "next steps, medicine changes, or lab follow-up with your doctor."
)

EMERGENCY_PATTERNS = [
    r"\bchest pain\b",
    r"\b(can'?t|cannot) breathe\b",
    r"\bunconscious\b",
    r"\bstroke\b",
    r"\bseizure\b",
    r"\bsevere bleeding\b",
    r"\boverdose\b",
    r"\bsuicidal\b",
    r"\bkill myself\b",
    r"\bheart attack\b",
    r"\bnot breathing\b",
    r"\bcollapsed\b",
    r"\bunresponsive\b",
]

INJECTION_PATTERNS = [
    r"\bignore previous\b",
    r"\bignore all instructions\b",
    r"\byou are now\b",
    r"\bpretend you are\b",
    r"\bforget everything\b",
    r"\bsystem prompt\b",
    r"\bjailbreak\b",
    r"\bdisregard\b",
    r"\boverride\b",
]

HARD_BOUNDARY_PATTERNS = [
    r"\bshould i change my dose\b",
    r"\bshould i stop taking\b",
    r"\bis it safe to mix\b",
    r"\bwhat dose should i take\b",
    r"\bincrease my dose\b",
    r"\bdecrease my dose\b",
]

DIAGNOSIS_PATTERNS = [
    r"you are suffering from\b",
    r"you are diagnosed with\b",
    r"you definitely have\b",
    r"clearly you have\b",
    r"this confirms you have\b",
    r"your condition is (?!unknown|unclear|not)",
]

DOSING_ADVICE_PATTERNS = [
    r"\byou should take\b",
    r"\byou need to take\b",
    r"\bstart taking\b",
    r"\bstop taking\b",
    r"\bincrease your dose\b",
    r"\bdecrease your dose\b",
    r"\btake\s+\d+\s*(mg|mcg|g|ml)\b",
]

FILE_CONTEXT_HINT_KEYWORDS = [
    "upload",
    "uploaded",
    "file",
    "report",
    "parse",
    "parsed",
    "extract",
    "extracted",
    "pdf",
    "document",
    "lab report",
    "why only",
    "how many labs",
    "did it read",
]


def _contains(text: str, patterns: List[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _safe_list(v: Any) -> List[Any]:
    return v if isinstance(v, list) else []


def _normalize_record_needs(raw: Any) -> Dict[str, bool]:
    base = {"labs": False, "visits": False, "medications": False, "files": False}

    if isinstance(raw, dict):
        for k in base:
            base[k] = bool(raw.get(k, False))
    elif isinstance(raw, (list, tuple, set)):
        lowered = {str(x).lower() for x in raw if x}
        base["labs"] = "labs" in lowered or "lab" in lowered
        base["visits"] = "visits" in lowered or "visit" in lowered
        base["medications"] = any(
            x in lowered for x in ("medications", "medication", "meds", "med")
        )
        base["files"] = "files" in lowered or "file" in lowered

    return base


# ── Fast local intent classification ─────────────────────────────────────────

def build_local_classification(question: str) -> Dict[str, Any]:
    """
    Local keyword-based classifier.

    This replaces the older Vertex-based classify_question() call so each chat
    uses only one Vertex request: the final answer generation.
    """
    q = question.lower()

    needs_labs = any(x in q for x in [
        "lab", "labs", "blood", "cholesterol", "ldl", "hdl",
        "triglyceride", "lipid", "glucose", "a1c", "hba1c",
        "thyroid", "tsh", "t3", "t4", "creatinine", "kidney",
        "renal", "gfr", "bun", "urea", "hemoglobin", "cbc",
        "platelet", "wbc", "rbc", "anemia", "vitamin d",
        "b12", "iron", "ferritin", "result", "results", "trend",
        "abnormal", "high", "low",
    ])

    needs_visits = any(x in q for x in [
        "visit", "doctor", "appointment", "symptom", "symptoms",
        "diagnosis", "diagnosed", "follow up", "follow-up",
        "history", "clinical note", "notes", "complaint",
    ])

    needs_meds = any(x in q for x in [
        "medication", "medications", "medicine", "medicines",
        "drug", "drugs", "dose", "dosage", "tablet", "pill",
        "metformin", "statin", "atorvastatin", "rosuvastatin",
        "insulin", "amlodipine", "lisinopril", "losartan",
        "levothyroxine",
    ])

    needs_files = any(x in q for x in [
        "file", "upload", "uploaded", "report", "pdf",
        "document", "scan", "attachment",
    ])

    # If user says "my" health question, pull broader context.
    patient_specific = any(x in q for x in [
        "my ", "mine", "me ", "for me", "my health", "my result",
        "my labs", "my medication", "my visit", "my report",
    ])

    if patient_specific and not any([needs_labs, needs_visits, needs_meds, needs_files]):
        needs_labs = True
        needs_meds = True
        needs_visits = True

    return {
        "needs_records": {
            "labs": needs_labs,
            "visits": needs_visits,
            "medications": needs_meds,
            "files": needs_files,
        },
        "visit_search_terms": [],
        "lab_search_terms": [],
        "medication_search_terms": [],
        "textbook_query": question,
    }


# ── PHI + Safety ─────────────────────────────────────────────────────────────

def apply_phi_guard(question: str) -> Dict[str, Any]:
    found = detect_phi(question)
    if any(found.values()):
        cleaned = redact_phi(question)
        types = [k for k, v in found.items() if v]
        return {"cleaned": cleaned, "phi_found": True, "phi_types": types}

    return {"cleaned": question, "phi_found": False, "phi_types": []}


def check_input_safety(question: str) -> Dict[str, Any]:
    if _contains(question, EMERGENCY_PATTERNS):
        return {
            "safe": False,
            "reason": "emergency",
            "response": "🚨 Call 911 immediately. HealthNav cannot assist with emergencies.",
        }

    if _contains(question, INJECTION_PATTERNS):
        return {
            "safe": False,
            "reason": "prompt_injection",
            "response": "That request is not supported.",
        }

    if _contains(question, HARD_BOUNDARY_PATTERNS):
        return {
            "safe": False,
            "reason": "clinical_boundary",
            "response": (
                "HealthNav cannot tell you to change, stop, or combine medicines. "
                "Please speak with your prescribing doctor."
            ),
        }

    return {"safe": True, "reason": "", "response": ""}


def check_output_safety(response: str) -> Dict[str, Any]:
    flags = []
    lowered = response.lower()

    for p in DIAGNOSIS_PATTERNS:
        if re.search(p, response, re.IGNORECASE):
            flags.append(f"diagnosis: {p}")

    if "doctor may decide" not in lowered and "ask your doctor" not in lowered:
        for p in DOSING_ADVICE_PATTERNS:
            if re.search(p, response, re.IGNORECASE):
                flags.append(f"dosing: {p}")

    return {"safe": len(flags) == 0, "flags": flags}


# ── Record retrieval ─────────────────────────────────────────────────────────

def retrieve_patient_records(
    patient_id: str,
    classification: Dict[str, Any],
    role: str,
    source_ip: Optional[str] = None,
    include_recent_files: bool = False,
) -> Tuple[str, str]:
    needs = _normalize_record_needs(classification.get("needs_records", {}))

    visits: List[Dict] = []
    labs: List[Dict] = []
    medications: List[Dict] = []
    files: List[Dict] = []
    accessed: List[str] = []

    try:
        if needs["visits"]:
            kw = _safe_list(classification.get("visit_search_terms"))
            visits = (
                search_visits_by_keyword(patient_id, kw, limit=6)
                if kw
                else get_recent_visits(patient_id, limit=6)
            ) or []
            if visits:
                accessed.append("visits")
    except Exception as exc:
        logger.exception("Visit retrieval failed: %s", exc)

    try:
        if needs["labs"]:
            terms = _safe_list(classification.get("lab_search_terms"))
            labs = (
                get_lab_by_name(patient_id, terms, limit=15)
                if terms
                else get_recent_labs(patient_id, limit=15)
            ) or []

            abnormal = get_abnormal_labs(patient_id, limit=8) or []
            seen = {
                (
                    str(r.get("test_name", "")).lower(),
                    str(r.get("test_date", "")),
                    str(r.get("test_value", "")),
                )
                for r in labs
            }

            for a in abnormal:
                k = (
                    str(a.get("test_name", "")).lower(),
                    str(a.get("test_date", "")),
                    str(a.get("test_value", "")),
                )
                if k not in seen:
                    labs.append(a)

            if labs:
                accessed.append("labs")
    except Exception as exc:
        logger.exception("Lab retrieval failed: %s", exc)

    try:
        if needs["medications"]:
            terms = _safe_list(classification.get("medication_search_terms"))
            medications = (
                get_medication_history(patient_id, terms)
                if terms
                else get_active_medications(patient_id)
            ) or []
            if medications:
                accessed.append("medications")
    except Exception as exc:
        logger.exception("Medication retrieval failed: %s", exc)

    try:
        if include_recent_files or needs["files"]:
            files = get_recent_files(patient_id, limit=RECENT_FILES_LIMIT) or []
            if files:
                accessed.append("files")
    except Exception as exc:
        logger.exception("File retrieval failed: %s", exc)

    if accessed:
        try:
            log_record_access(
                patient_id=patient_id,
                role=role,
                record_types=sorted(set(accessed)),
                source_ip=source_ip,
            )
        except Exception:
            pass

    if not any([visits, labs, medications, files]):
        return "", ""

    try:
        formatted = format_full_patient_context(
            visits=visits or None,
            labs=labs or None,
            medications=medications or None,
            files=files or None,
            patient_role=role,
        )
    except Exception as exc:
        logger.exception("format_full_patient_context failed: %s", exc)
        formatted = ""

    trend_summary = ""
    if labs:
        try:
            names = sorted(
                {str(r.get("test_name", "")).strip() for r in labs if r.get("test_name")}
            )
            histories: List[Dict] = []
            for name in names:
                histories.extend(get_lab_history(patient_id, name, limit=20) or [])

            if histories:
                trend_summary = format_trend_summary(analyze_all_labs(histories))
        except Exception as exc:
            logger.exception("Trend analysis failed: %s", exc)

    return formatted.strip(), trend_summary.strip()


# ── Textbook retrieval ───────────────────────────────────────────────────────

def retrieve_textbook_chunks(question: str, classification: Dict[str, Any]) -> List[Dict]:
    query = classification.get("textbook_query", question)
    normalized = normalize_query(query)

    # No Vertex query expansion here. This keeps the system to one Vertex call.
    expanded = normalized

    chunks = hybrid_search(normalized, expanded, top_k=TEXTBOOK_TOP_K)

    result = []
    for c in chunks:
        text = (c.get("chunk_text") or "").strip()
        if not text:
            continue

        if len(text) > MAX_CHUNK_CHARS:
            text = text[:MAX_CHUNK_CHARS].rstrip() + " ..."

        result.append({
            "source": c.get("source_file", "Unknown").replace(".pdf", "").replace("_", " ").title(),
            "page": c.get("page_number", "?"),
            "text": text,
        })

    return result


def _fmt_book_chunks(chunks: Optional[List[Dict]]) -> str:
    if not chunks:
        return "No textbook evidence retrieved."

    parts = []
    for i, c in enumerate(chunks[:TEXTBOOK_TOP_K], 1):
        text = c.get("text", "").strip()
        if text:
            parts.append(
                f"[Source {i}] {c.get('source', '?')}, Page {c.get('page', '?')}\n{text}"
            )

    return "\n\n".join(parts) if parts else "No textbook evidence retrieved."


# ── Prompt building ──────────────────────────────────────────────────────────

def _build_prompt(
    question: str,
    mode: str,
    patient_ctx: str,
    textbook_ctx: str,
    attachment_ctx: str,
    role: str,
    no_records_note: str = "",
) -> str:
    role_instr = (
        "You are answering a clinician. Use structured, clinically careful reasoning."
        if role == "clinician"
        else "You are answering a patient. Use calm, simple, patient-friendly language."
    )

    base = (
        "You are HealthNav, a patient-facing clinical explanation assistant.\n\n"
        f"{role_instr}\n\n"
        "Rules:\n"
        "- Do not claim to be a doctor.\n"
        "- Do not provide a diagnosis.\n"
        "- Do not prescribe treatment or advise dose changes.\n"
        "- Use the patient's records when provided.\n"
        "- Use textbook evidence when provided.\n"
        "- If patient-specific records are unavailable, answer as general medical education.\n"
        "- Explain in simple language.\n"
        "- Mention what is known, what is uncertain, and what should be discussed with a clinician.\n"
        "- End with 'Questions to ask your doctor' with 3-5 bullet points.\n"
        "- Do not add a long disclaimer — it is appended automatically.\n"
    )

    sys_note = f"\nSYSTEM NOTE: {no_records_note}\n" if no_records_note else ""

    if mode == "patient_summary":
        return (
            f"{base}\n\nTask: PATIENT SUMMARY\n\n"
            f"Patient context:\n{patient_ctx}\n\n"
            f"Attachment:\n{attachment_ctx}\n\n"
            f"Question:\n{question}\n{sys_note}"
        )

    if mode == "patient_interpretation":
        return (
            f"{base}\n\nTask: PATIENT INTERPRETATION\n\n"
            f"Patient context:\n{patient_ctx}\n\n"
            f"Attachment:\n{attachment_ctx}\n\n"
            f"Textbook evidence:\n{textbook_ctx}\n\n"
            f"Question:\n{question}\n{sys_note}"
        )

    if mode == "attachment_assisted_explanation":
        return (
            f"{base}\n\nTask: ATTACHMENT-ASSISTED EXPLANATION\n\n"
            f"Patient context:\n{patient_ctx}\n\n"
            f"Attachment:\n{attachment_ctx}\n\n"
            f"Textbook evidence:\n{textbook_ctx}\n\n"
            f"Question:\n{question}\n{sys_note}"
        )

    return (
        f"{base}\n\nTask: GENERAL MEDICAL EXPLANATION\n\n"
        f"Patient context:\n{patient_ctx}\n\n"
        f"Attachment:\n{attachment_ctx}\n\n"
        f"Textbook evidence:\n{textbook_ctx}\n\n"
        f"Question:\n{question}\n{sys_note}"
    )


def _classify_mode(question: str, has_attachment: bool) -> str:
    if has_attachment:
        return "attachment_assisted_explanation"

    q = question.lower()

    summary_kw = [
        "summarize",
        "summary",
        "my labs",
        "my records",
        "my medications",
        "my visits",
        "my results",
        "my trends",
    ]

    interpret_kw = [
        "why is my",
        "what explains",
        "does this mean",
        "is this dangerous",
        "is this bad",
        "interpret",
        "abnormal",
        "trend in my",
        "my cholesterol",
        "my glucose",
        "my a1c",
        "my tsh",
        "my creatinine",
    ]

    if any(k in q for k in summary_kw):
        return "patient_summary"

    if any(k in q for k in interpret_kw):
        return "patient_interpretation"

    return "general_medical_explanation"


# ── Vertex call ──────────────────────────────────────────────────────────────

async def _call_vertex(prompt: str) -> str:
    client = get_vertex_client()
    model_name = get_vertex_model_name()
    last_exc: Exception = RuntimeError("No attempts")

    max_attempts = max(_VERTEX_RETRIES + 1, 3)

    for attempt in range(1, max_attempts + 1):
        try:
            await asyncio.sleep(1)

            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )

            return (resp.text or "").strip()

        except Exception as exc:
            last_exc = exc
            err = str(exc).lower()

            logger.warning("Vertex attempt %s failed: %s", attempt, exc)

            if (
                "429" in err
                or "quota" in err
                or "resource_exhausted" in err
                or "too many requests" in err
                or "cancelled" in err
            ):
                wait_seconds = min(10 * attempt, 30)
                logger.warning("Rate limit/cancel detected. Waiting %s seconds.", wait_seconds)
                await asyncio.sleep(wait_seconds)
                continue

            raise exc

    raise last_exc


# ── Main entry point ─────────────────────────────────────────────────────────

async def answer_question(
    raw_question: str,
    patient_id: str,
    role: str = "patient",
    source_ip: Optional[str] = None,
    attachment_text: str = "",
    attachment_name: Optional[str] = None,
) -> Dict[str, Any]:
    ensure_tables_exist()

    raw_question = (raw_question or "").strip()

    if not raw_question:
        return {
            "answer": "Please enter a question.",
            "citations": [],
            "phi_warning": False,
            "blocked": False,
            "block_reason": "",
            "mode": None,
            "sources": {
                "used_records": False,
                "used_textbook": False,
                "used_attachment": False,
            },
        }

    try:
        save_chat_message(patient_id=patient_id, role="user", message_text=raw_question)
    except Exception:
        pass

    phi = apply_phi_guard(raw_question)
    cleaned = (phi["cleaned"] or "").strip()
    q_hash = hashlib.sha256(cleaned.encode()).hexdigest()[:16]

    safety = check_input_safety(cleaned)
    if not safety["safe"]:
        try:
            log_chat_query(
                patient_id=patient_id,
                role=role,
                question_hash=q_hash,
                phi_detected=phi["phi_found"],
                phi_types=phi["phi_types"],
                blocked=True,
                sources_used={
                    "used_records": False,
                    "used_textbook": False,
                    "used_attachment": False,
                },
                source_ip=source_ip,
            )
        except Exception:
            pass

        try:
            save_chat_message(
                patient_id=patient_id,
                role="assistant",
                message_text=safety["response"],
                answer_mode="blocked",
            )
        except Exception:
            pass

        return {
            "answer": safety["response"],
            "citations": [],
            "phi_warning": phi["phi_found"],
            "blocked": True,
            "block_reason": safety["reason"],
            "mode": None,
            "sources": {
                "used_records": False,
                "used_textbook": False,
                "used_attachment": False,
            },
        }

    normalized = normalize_query(cleaned)
    has_attachment = bool((attachment_text or "").strip())

    # Fast local classification — avoids extra Vertex calls.
    classification = build_local_classification(normalized)

    mode = _classify_mode(normalized, has_attachment)
    include_files = any(kw in normalized.lower() for kw in FILE_CONTEXT_HINT_KEYWORDS)

    try:
        patient_ctx, trend = retrieve_patient_records(
            patient_id=patient_id,
            classification=classification,
            role=role,
            source_ip=source_ip,
            include_recent_files=include_files,
        )
    except Exception as exc:
        logger.exception("Record retrieval failed: %s", exc)
        patient_ctx, trend = "", ""

    has_records = bool(patient_ctx.strip())

    if not has_records and not has_attachment:
        mode = "general_medical_explanation"
    elif has_attachment and not has_records:
        mode = "attachment_assisted_explanation"

    patient_ctx_text = (
        "PATIENT RECORDS:\n"
        + patient_ctx.strip()
        + ("\n\nLAB TRENDS:\n" + trend.strip() if trend.strip() else "")
        if patient_ctx.strip()
        else "No patient-specific context available."
    )

    attachment_ctx_text = (
        f"ATTACHED DOCUMENT ({attachment_name or 'file'}):\n"
        f"{(attachment_text or '')[:MAX_ATTACHMENT_CHARS].strip()}"
        if has_attachment
        else "No attached document."
    )

    book_chunks: List[Dict] = []
    if mode in (
        "patient_interpretation",
        "general_medical_explanation",
        "attachment_assisted_explanation",
    ):
        try:
            book_chunks = retrieve_textbook_chunks(normalized, classification)
        except Exception as exc:
            logger.exception("Textbook retrieval failed: %s", exc)

    textbook_ctx = (
        _fmt_book_chunks(book_chunks)
        if book_chunks
        else "No textbook evidence retrieved."
    )

    no_records_note = (
        "No matching stored patient records were found. Answer as general medical education."
        if not has_records
        else ""
    )

    prompt = _build_prompt(
        question=cleaned,
        mode=mode,
        patient_ctx=patient_ctx_text,
        textbook_ctx=textbook_ctx,
        attachment_ctx=attachment_ctx_text,
        role=role,
        no_records_note=no_records_note,
    )

    used_records = has_records
    used_textbook = bool(book_chunks)
    used_attachment = has_attachment

    try:
        ai_response = await _call_vertex(prompt)
    except Exception as exc:
        logger.error("Vertex generation failed: %s", exc)
        fallback = "I'm having trouble generating a response right now. Please try again later."

        try:
            save_chat_message(
                patient_id=patient_id,
                role="assistant",
                message_text=fallback,
                answer_mode=mode,
                used_records=used_records,
                used_textbook=used_textbook,
                used_attachment=used_attachment,
            )
        except Exception:
            pass

        return {
            "answer": fallback,
            "citations": [],
            "phi_warning": phi["phi_found"],
            "blocked": True,
            "block_reason": "generation_failure",
            "mode": mode,
            "sources": {
                "used_records": used_records,
                "used_textbook": used_textbook,
                "used_attachment": used_attachment,
            },
        }

    ai_response = (ai_response or "").strip() or "I could not generate a clear answer."

    out_safety = check_output_safety(ai_response)
    if not out_safety["safe"]:
        blocked_answer = (
            "I couldn't safely phrase that answer. Please ask your doctor directly."
            + SAFETY_DISCLAIMER
        )

        try:
            save_chat_message(
                patient_id=patient_id,
                role="assistant",
                message_text=blocked_answer,
                answer_mode=mode,
            )
        except Exception:
            pass

        return {
            "answer": blocked_answer,
            "citations": [],
            "phi_warning": phi["phi_found"],
            "blocked": True,
            "block_reason": "output_safety",
            "mode": mode,
            "sources": {
                "used_records": used_records,
                "used_textbook": used_textbook,
                "used_attachment": used_attachment,
            },
        }

    final = ai_response + SAFETY_DISCLAIMER

    try:
        log_chat_query(
            patient_id=patient_id,
            role=role,
            question_hash=q_hash,
            phi_detected=phi["phi_found"],
            phi_types=phi["phi_types"],
            blocked=False,
            sources_used={
                "used_records": used_records,
                "used_textbook": used_textbook,
                "used_attachment": used_attachment,
            },
            source_ip=source_ip,
        )
    except Exception:
        pass

    try:
        save_chat_message(
            patient_id=patient_id,
            role="assistant",
            message_text=final,
            answer_mode=mode,
            used_records=used_records,
            used_textbook=used_textbook,
            used_attachment=used_attachment,
        )
    except Exception:
        pass

    return {
        "answer": final,
        "citations": [],
        "phi_warning": phi["phi_found"],
        "blocked": False,
        "block_reason": "",
        "mode": mode,
        "sources": {
            "used_records": used_records,
            "used_textbook": used_textbook,
            "used_attachment": used_attachment,
        },
    }
