"""
Rules first, Vertex AI fallback for ambiguous questions.
Bias toward personalized answers when a logged-in patient is asking.
"""
from __future__ import annotations

import json
import logging
import re

from vertex_client import get_vertex_client, get_vertex_model_name

logger = logging.getLogger(__name__)

PERSONAL_SIGNALS = [
    "my", "i have", "i had", "i was", "my last", "my result", "my lab", "my blood", "my report", "my history",
    "my visit", "my doctor said", "last time", "previous", "i was told", "my medication", "i take", "i am on", "i've been",
    "i'm on", "my test", "my scan", "my prescription", "my record",
]
GENERAL_SIGNALS = [
    "what is", "what are", "how does", "why does", "explain", "what causes", "what happens", "how do",
    "tell me about", "what does", "meaning of", "define", "mechanism", "symptoms of", "cause of", "difference between",
]
LAB_SIGNALS = [
    "wbc", "rbc", "hemoglobin", "hematocrit", "platelet", "creatinine", "glucose", "hba1c", "tsh", "t3", "t4",
    "sodium", "potassium", "cholesterol", "ldl", "hdl", "triglyceride", "bilirubin", "alt", "ast", "albumin",
    "urea", "uric acid", "esr", "crp", "ferritin", "lab", "labs", "blood test", "report", "result", "level",
]
VISIT_SIGNALS = [
    "visit", "appointment", "checkup", "doctor said", "told me", "diagnosed", "noted", "observed", "complaint",
    "symptom", "admitted", "discharge", "history", "clinical note", "summary",
]
MED_SIGNALS = [
    "medication", "medicine", "drug", "tablet", "capsule", "pill", "injection", "prescription", "dose", "dosage",
    "taking", "prescribed", "metformin", "amlodipine", "aspirin", "lisinopril", "atorvastatin", "omeprazole",
    "paracetamol", "ibuprofen",
]


def _extract_terms(q: str, source: list[str], min_len: int) -> list[str]:
    # BUG FIX: Use word-boundary regex instead of plain substring match to avoid false
    # positives like "history" matching in "symptoms of hypertension" or "family history".
    return [s for s in source if len(s) >= min_len and re.search(rf"\b{re.escape(s)}\b", q)]


def _rule_classify(question: str) -> dict | None:
    q = question.lower()
    # BUG FIX: Use word-boundary regex for all signal checks to prevent false positives.
    # e.g. "history" in VISIT_SIGNALS was matching "family history of diabetes" via substring.
    is_personal = any(re.search(rf"\b{re.escape(s)}\b", q) for s in PERSONAL_SIGNALS)
    is_general = any(re.search(rf"\b{re.escape(s)}\b", q) for s in GENERAL_SIGNALS)
    needs_labs = any(re.search(rf"\b{re.escape(s)}\b", q) for s in LAB_SIGNALS)
    needs_visits = any(re.search(rf"\b{re.escape(s)}\b", q) for s in VISIT_SIGNALS)
    needs_meds = any(re.search(rf"\b{re.escape(s)}\b", q) for s in MED_SIGNALS)

    if is_personal or (needs_labs or needs_visits or needs_meds):
        return {
            "is_personal": True,
            "is_general": True,
            "needs_records": {
                "visits": needs_visits or not (needs_labs or needs_meds),
                "labs": needs_labs or ("result" in q or "report" in q),
                "medications": needs_meds,
            },
            "visit_search_terms": _extract_terms(q, VISIT_SIGNALS, 5),
            "lab_search_terms": _extract_terms(q, LAB_SIGNALS, 3),
            "medication_search_terms": _extract_terms(q, MED_SIGNALS, 5),
            "textbook_query": question,
            "classified_by": "rules",
        }

    if is_general:
        return {
            "is_personal": False,
            "is_general": True,
            "needs_records": {"visits": False, "labs": False, "medications": False},
            "visit_search_terms": [],
            "lab_search_terms": [],
            "medication_search_terms": [],
            "textbook_query": question,
            "classified_by": "rules",
        }
    return None


def _vertex_classify(question: str) -> dict:
    client = get_vertex_client()
    model_name = get_vertex_model_name()
    prompt = f"""You are classifying a patient's question for a medical portal.
Determine:
1. Is this about the patient's own records or history? (is_personal)
2. Does it also need general medical explanation? (is_general)
3. Which record types are needed? (visits, labs, medications)
4. Extract lab names, medication names, and visit topics if present.
5. Suggest one textbook search query.
Return ONLY valid JSON with keys: is_personal, is_general, needs_records, lab_search_terms, medication_search_terms, visit_search_terms, textbook_query.
Patient question: {question}"""
    try:
        response = client.models.generate_content(model=model_name, contents=prompt)
        text = re.sub(r"```json|```", "", (response.text or "").strip()).strip()
        parsed = json.loads(text)
        parsed["classified_by"] = "vertex"
        return parsed
    except Exception as exc:
        logger.warning("Vertex classifier failed: %s", exc)
        return {
            "is_personal": True,
            "is_general": True,
            "needs_records": {"visits": True, "labs": True, "medications": True},
            "lab_search_terms": [],
            "medication_search_terms": [],
            "visit_search_terms": [],
            "textbook_query": question,
            "classified_by": "fallback_personalized",
        }


def classify_question(question: str) -> dict:
    result = _rule_classify(question)
    return result if result is not None else _vertex_classify(question)
