"""
search_chunks.py — hybrid semantic + keyword search over medical_chunks.
Falls back gracefully if the table doesn't exist yet.
"""
from __future__ import annotations

import logging
from typing import Dict, List

import psycopg2

from config import get_database_config

logger = logging.getLogger(__name__)

_embed_model = None
MIN_KEYWORD_SCORE = 0.20
KEYWORD_FETCH_LIMIT = 120

STOP_WORDS = {
    "what", "does", "mean", "that", "this", "with", "have", "from", "your",
    "when", "will", "been", "they", "were", "into", "some", "about", "high",
    "level", "test", "result", "show", "tell", "know", "make", "look", "find",
    "cause", "causes", "caused", "why", "how", "which", "where", "who",
    "explain", "describe",
}


def _get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer("all-mpnet-base-v2")
    return _embed_model


def _get_connection():
    return psycopg2.connect(**get_database_config().as_psycopg_kwargs())


def _vec_to_pg(values: list) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"


def _chunks_table_ready(cur) -> bool:
    cur.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public'
              AND table_name='medical_chunks'
              AND column_name='embedding'
        );
    """)
    return cur.fetchone()[0]


def vector_search(cur, query_text: str, top_k: int = 10) -> List[Dict]:
    if not _chunks_table_ready(cur):
        return []
    model = _get_embed_model()
    qvec = _vec_to_pg(model.encode(query_text).tolist())
    cur.execute(
        """
        SELECT source_file, page_number, chunk_text,
               1 - (embedding <=> %s::vector) AS similarity
        FROM medical_chunks
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s;
        """,
        (qvec, qvec, top_k * 3),
    )
    seen: set = set()
    results = []
    for row in cur.fetchall():
        key = (row[0], row[2][:150])
        if key not in seen:
            seen.add(key)
            results.append({
                "source_file": row[0], "page_number": row[1],
                "chunk_text": row[2], "similarity": float(row[3]),
                "retrieval": "vector",
            })
        if len(results) >= top_k:
            break
    return results


def keyword_search(cur, expanded_query: str, top_k: int = 10) -> List[Dict]:
    if not _chunks_table_ready(cur):
        return []
    words = [
        w for w in expanded_query.lower().split()
        if len(w) > 3 and w not in STOP_WORDS
    ]
    if not words:
        return []
    conditions = " OR ".join(["chunk_text ILIKE %s"] * len(words))
    cur.execute(
        f"""
        SELECT source_file, page_number, chunk_text
        FROM medical_chunks
        WHERE {conditions}
        LIMIT {KEYWORD_FETCH_LIMIT};
        """,
        [f"%{w}%" for w in words],
    )
    scored = []
    for source_file, page_number, chunk_text in cur.fetchall():
        lower = chunk_text.lower()
        score = sum(1 for w in words if w in lower) / len(words)
        if score >= MIN_KEYWORD_SCORE:
            scored.append({
                "source_file": source_file, "page_number": page_number,
                "chunk_text": chunk_text, "similarity": score,
                "retrieval": "keyword",
            })
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_k]


def hybrid_search(query_text: str, expanded_query: str, top_k: int = 12) -> List[Dict]:
    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            vec = vector_search(cur, query_text, top_k=top_k)
            kw = keyword_search(cur, expanded_query, top_k=max(top_k * 2, 20))
    finally:
        conn.close()

    merged: List[Dict] = []
    seen: set = set()
    for result in sorted(vec + kw, key=lambda x: x["similarity"], reverse=True):
        key = (result["source_file"], result["chunk_text"][:150])
        if key not in seen:
            seen.add(key)
            merged.append(result)
        if len(merged) >= top_k:
            break

    logger.info("hybrid_search returned %d chunks", len(merged))
    return merged
