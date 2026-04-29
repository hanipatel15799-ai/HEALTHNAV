# HEALTHNAV

# HealthNav — Health AI Portal

Production-ready health document intelligence system.
FastAPI + PostgreSQL/pgvector + AWS BEDROCK (Gemini 2.5 Flash).

---

## Architecture

```
Upload (PDF/Image/CSV)
  └── report_parser.py
        ├── utils/pdf_utils.py     → table-aware PDF extraction (find_tables)
        ├── utils/ocr_utils.py     → OCR fallback for scanned pages
        ├── utils/validation.py    → date normalisation, row validation
        └── AWS BEDROCK             → structured JSON extraction
              ↓
      patient_labs / patient_visits / patient_medications
      (per-row SAVEPOINTs — one bad row never kills others)
              ↓
Chat (ask HealthNav)
  └── answer_with_ai.py
        ├── patient_record_retrieval.py → longitudinal patient history
        ├── search_chunks.py            → pgvector RAG (medical textbooks)
        └── Vertex AI                   → grounded safe answer
```

---

## Quick start (local)

### 1. Prerequisites
- Python 3.11+
- PostgreSQL 15+ with pgvector: `CREATE EXTENSION vector;`
- Google Cloud project with Vertex AI API enabled
- Tesseract OCR (optional, for scanned PDFs): `apt-get install tesseract-ocr`

### 2. Install
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure
```bash
cp .env.example .env
# Edit .env — fill in DB credentials, GCP project ID, APP_SECRET
```

### 4. Database
```bash
# Create DB
psql -U postgres -c "CREATE DATABASE healthnav;"

# Run schema (idempotent — safe to re-run)
psql -U postgres -d healthnav -f db/schema.sql
```

### 5. Run
```bash
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000 — login with `demo` / `demo1234!!`

---

## What to do if you still see "0 lab results"

Run this SQL on your existing DB first:
```sql
ALTER TABLE patient_file_extractions ADD COLUMN IF NOT EXISTS visual_summary TEXT;
ALTER TABLE patient_file_extractions ADD COLUMN IF NOT EXISTS source_kind    TEXT;
```

Then re-upload your file or use the Re-parse button.

Check the server logs — you will now see:
```
INFO: PDF extraction START: 16 pages
INFO: Page 1: table+text (384 table chars, 959 plain chars)
INFO: PDF extraction DONE: total_chars=23689 mode_counts={'table+text': 16}
INFO: Detected report date: 2024-11-23
INFO: Vertex AI extraction START: model=gemini-2.5-flash text_chars=23689
INFO: Vertex AI extraction RESULT: confidence='high' labs_returned=42 visits=0 meds=0
INFO: Sample lab row[0]: {"test_name": "HDL Cholesterol", "test_value": "38.00", ...}
INFO: insert_labs_safe DONE: patient=... returned=42 inserted=38 skipped=4
INFO: parse_and_store COMPLETE: labs_returned=42 labs_inserted=38
```

---

## Optional: Load medical textbook PDFs (RAG)

```bash
mkdir -p data/medical_pdfs
# Copy your PDFs into data/medical_pdfs/
python chunk_text.py          # Extract + chunk text
python load_chunks_to_db.py   # Embed + store in pgvector
```

---

## Cloud Run deployment

```bash
# One-time: store secrets
echo -n "your-secret" | gcloud secrets create healthnav-app-secret --data-file=-

# Deploy
gcloud builds submit --config cloudbuild.yaml
```

---

## File placement guide

```
healthnav/
├── main.py                     FastAPI app
├── report_parser.py            Document extraction pipeline
├── answer_with_ai.py           AI reasoning pipeline
├── patient_record_retrieval.py DB read/write for patient data
├── search_chunks.py            pgvector hybrid search
├── vertex_client.py            Vertex AI singleton client
├── phi_guard.py                PHI detection + redaction
├── auth.py                     bcrypt auth + session tokens
├── config.py                   Environment config
├── api_models.py               Pydantic schemas
├── audit.py                    HIPAA audit logging
├── intent_classifier.py        Query intent classification
├── normalize_query.py          Query normalisation
├── rewrite_query_with_ai.py    Query expansion for RAG
├── trend_analysis.py           Lab trend detection
├── context_builder.py          Minimum-necessary context builder
├── chunk_text.py               PDF → chunks for RAG
├── load_chunks_to_db.py        Embed + load chunks to pgvector
├── db/
│   ├── schema.sql              Canonical DB schema
│   └── db.py                   Connection factory
├── utils/
│   ├── pdf_utils.py            Table-aware PDF extraction
│   ├── ocr_utils.py            Tesseract OCR wrapper
│   └── validation.py           Date + row validation
├── static/
│   └── index.html              Full portal frontend
├── requirements.txt
├── .env.example
├── Dockerfile
└── cloudbuild.yaml
```
