"""
Normalize patient-friendly queries before classification / retrieval.
"""
from __future__ import annotations

import re
from typing import List

ABBREVIATIONS = {
    "bp": "blood pressure",
    "bs": "blood sugar",
    "sob": "shortness of breath",
    "hr": "heart rate",
    "temp": "temperature",
    "meds": "medications",
    "rx": "prescription",
    "htn": "hypertension",
    "dm": "diabetes mellitus",
    "uti": "urinary tract infection",
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on",
    "at", "is", "it", "this", "that", "my", "me", "with", "about",
    "from", "what", "why", "how", "when", "can", "could", "would",
    "should", "do", "does", "did", "am", "are", "was", "were",
}


def normalize_query(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    for short, full in ABBREVIATIONS.items():
        text = re.sub(rf"\b{re.escape(short)}\b", full, text)
    return re.sub(r"\s+", " ", text).strip()


def extract_keywords(text: str, min_len: int = 3) -> List[str]:
    seen = set()
    out: List[str] = []
    for token in normalize_query(text).split():
        if len(token) < min_len or token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out
