from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_batch
from sentence_transformers import SentenceTransformer

from config import get_database_config

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CSV_FILE = Path(os.getenv("CHUNKS_OUTPUT_FILE", "data/chunks.csv"))
REBUILD_TABLE = os.getenv("REBUILD_MEDICAL_CHUNKS", "false").lower() == "true"
_embed_model = None


def get_embed_model() -> SentenceTransformer:
    global _embed_model
    if _embed_model is None:
        _embed_model = SentenceTransformer("all-mpnet-base-v2")
    return _embed_model


def get_connection():
    return psycopg2.connect(**get_database_config().as_psycopg_kwargs())


def validate_csv(df: pd.DataFrame) -> None:
    expected_cols = {"chunk_id", "source_file", "page_number", "chunk_index", "chunk_text"}
    missing = expected_cols - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {', '.join(sorted(missing))}")


def table_exists(cur, table_name: str) -> bool:
    cur.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
        );
    """, (table_name,))
    return cur.fetchone()[0]


def get_columns(cur, table_name: str) -> set:
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s;
    """, (table_name,))
    return {row[0] for row in cur.fetchall()}


def recreate_medical_chunks(cur) -> None:
    logger.warning("Rebuilding medical_chunks...")
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    cur.execute("DROP TABLE IF EXISTS medical_chunks;")
    cur.execute("""
        CREATE TABLE medical_chunks (
            id SERIAL PRIMARY KEY,
            chunk_id TEXT UNIQUE NOT NULL,
            source_file TEXT NOT NULL,
            page_number INT NOT NULL,
            chunk_index INT NOT NULL,
            chunk_text TEXT NOT NULL,
            embedding VECTOR(768)
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_medical_chunks_source_page ON medical_chunks(source_file, page_number);")


def ensure_correct_schema(cur) -> None:
    required_cols = {"id", "chunk_id", "source_file", "page_number", "chunk_index", "chunk_text", "embedding"}
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    if not table_exists(cur, "medical_chunks"):
        recreate_medical_chunks(cur)
        return

    existing = get_columns(cur, "medical_chunks")
    if required_cols.issubset(existing):
        return

    if REBUILD_TABLE:
        recreate_medical_chunks(cur)
    else:
        raise RuntimeError(
            "medical_chunks schema is outdated. Set REBUILD_MEDICAL_CHUNKS=true in .env to rebuild."
        )


def vector_to_pg(value) -> str:
    return "[" + ",".join(f"{float(x):.8f}" for x in value) + "]"


def load_chunks(cur, df: pd.DataFrame) -> None:
    model = get_embed_model()
    model.max_seq_length = 384
    texts = df["chunk_text"].fillna("").astype(str).tolist()
    logger.info("Generating embeddings for %d chunks...", len(texts))
    embeddings = model.encode(texts, batch_size=32, show_progress_bar=True, convert_to_numpy=True)

    records = []
    for i, (_, row) in enumerate(df.iterrows()):
        records.append(
            (
                str(row["chunk_id"]),
                str(row["source_file"]),
                int(row["page_number"]),
                int(row["chunk_index"]),
                str(row["chunk_text"]),
                vector_to_pg(embeddings[i]),
            )
        )

    execute_batch(
        cur,
        """
        INSERT INTO medical_chunks (chunk_id, source_file, page_number, chunk_index, chunk_text, embedding)
        VALUES (%s, %s, %s, %s, %s, %s::vector)
        ON CONFLICT (chunk_id) DO UPDATE SET
            source_file = EXCLUDED.source_file,
            page_number = EXCLUDED.page_number,
            chunk_index = EXCLUDED.chunk_index,
            chunk_text = EXCLUDED.chunk_text,
            embedding = EXCLUDED.embedding;
        """,
        records,
        page_size=100,
    )
    logger.info("Loaded %d chunks into medical_chunks", len(records))


def main() -> None:
    if not CSV_FILE.exists():
        raise FileNotFoundError(f"Chunk CSV not found: {CSV_FILE}")

    df = pd.read_csv(CSV_FILE)
    validate_csv(df)

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                ensure_correct_schema(cur)
                load_chunks(cur, df)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
