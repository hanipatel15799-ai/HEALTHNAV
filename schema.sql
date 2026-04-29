-- Place at: db/schema.sql
-- ─────────────────────────────────────────────────────────────────────────────
-- HealthNav canonical schema  v2 (production-ready, idempotent)
-- Run: psql -U postgres -d healthnav -f db/schema.sql
-- Safe to re-run — all statements use IF NOT EXISTS / DO NOTHING
-- ─────────────────────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- ── Users ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS patient_users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT UNIQUE,
    full_name     TEXT,
    patient_id    TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_active     BOOLEAN DEFAULT TRUE,
    role          TEXT DEFAULT 'patient',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- ── Patient profiles ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS patient_profiles (
    id            SERIAL PRIMARY KEY,
    patient_id    TEXT UNIQUE NOT NULL,
    full_name     TEXT,
    date_of_birth DATE,
    sex           TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_profiles_pid ON patient_profiles(patient_id);

-- ── Labs ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS patient_labs (
    id              SERIAL PRIMARY KEY,
    patient_id      TEXT NOT NULL,
    test_date       DATE NOT NULL,
    test_name       TEXT NOT NULL,
    test_value      TEXT,
    unit            TEXT,
    reference_range TEXT,
    is_abnormal     BOOLEAN DEFAULT FALSE,
    lab_name        TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_patient_lab UNIQUE (patient_id, test_date, test_name, test_value)
);
CREATE INDEX IF NOT EXISTS idx_labs_patient  ON patient_labs(patient_id);
CREATE INDEX IF NOT EXISTS idx_labs_date     ON patient_labs(patient_id, test_date DESC);
CREATE INDEX IF NOT EXISTS idx_labs_name     ON patient_labs(patient_id, test_name);
CREATE INDEX IF NOT EXISTS idx_labs_abnormal ON patient_labs(patient_id, is_abnormal);

-- ── Visits ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS patient_visits (
    id              SERIAL PRIMARY KEY,
    patient_id      TEXT NOT NULL,
    visit_date      DATE NOT NULL,
    visit_type      TEXT,
    chief_complaint TEXT,
    clinical_notes  TEXT,
    doctor_name     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_patient_visit UNIQUE (patient_id, visit_date, visit_type, chief_complaint)
);
CREATE INDEX IF NOT EXISTS idx_visits_patient ON patient_visits(patient_id);
CREATE INDEX IF NOT EXISTS idx_visits_date    ON patient_visits(patient_id, visit_date DESC);

-- ── Medications ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS patient_medications (
    id                 SERIAL PRIMARY KEY,
    patient_id         TEXT NOT NULL,
    medication_name    TEXT NOT NULL,
    dosage             TEXT,
    frequency          TEXT,
    start_date         DATE,
    end_date           DATE,
    prescribing_doctor TEXT,
    indication         TEXT,
    is_active          BOOLEAN DEFAULT TRUE,
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_patient_med UNIQUE (patient_id, medication_name, dosage, frequency, start_date)
);
CREATE INDEX IF NOT EXISTS idx_meds_patient ON patient_medications(patient_id);
CREATE INDEX IF NOT EXISTS idx_meds_active  ON patient_medications(patient_id, is_active);

-- ── Patient files ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS patient_files (
    id                SERIAL PRIMARY KEY,
    patient_id        TEXT NOT NULL,
    category          TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    stored_filename   TEXT NOT NULL,
    file_path         TEXT NOT NULL,
    content_type      TEXT,
    notes             TEXT,
    uploaded_at       TIMESTAMPTZ DEFAULT NOW(),
    parse_status      TEXT DEFAULT 'pending',
    parse_report_type TEXT,
    parse_confidence  TEXT,
    parse_notes       TEXT,
    labs_parsed       INT DEFAULT 0,
    visits_parsed     INT DEFAULT 0,
    meds_parsed       INT DEFAULT 0,
    parsed_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_files_patient ON patient_files(patient_id, uploaded_at DESC);

-- ── File extractions ──────────────────────────────────────────────────────────
-- CRITICAL: visual_summary and source_kind MUST exist — code inserts both
CREATE TABLE IF NOT EXISTS patient_file_extractions (
    id               SERIAL PRIMARY KEY,
    file_id          INTEGER REFERENCES patient_files(id) ON DELETE CASCADE,
    patient_id       TEXT NOT NULL,
    extraction_mode  TEXT NOT NULL,  -- 'raw_extract' | 'structured_extract'
    raw_text         TEXT,
    interpreted_text TEXT,
    visual_summary   TEXT,           -- required by save_file_extraction()
    source_kind      TEXT,           -- required by save_file_extraction()
    created_at       TIMESTAMPTZ DEFAULT NOW()
);
-- Additive migration for existing deployments (safe to run multiple times)
ALTER TABLE patient_file_extractions ADD COLUMN IF NOT EXISTS visual_summary TEXT;
ALTER TABLE patient_file_extractions ADD COLUMN IF NOT EXISTS source_kind    TEXT;

CREATE INDEX IF NOT EXISTS idx_extractions_file    ON patient_file_extractions(file_id);
CREATE INDEX IF NOT EXISTS idx_extractions_patient ON patient_file_extractions(patient_id);

-- ── Auth sessions ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS auth_sessions (
    session_token TEXT PRIMARY KEY,  -- SHA-256 hash, never plaintext
    user_id       TEXT NOT NULL,
    username      TEXT NOT NULL,
    patient_id    TEXT NOT NULL,
    role          TEXT NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    is_revoked    BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_sessions_patient ON auth_sessions(patient_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry  ON auth_sessions(expires_at);

-- ── Rate limit ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rate_limit_log (
    id           SERIAL PRIMARY KEY,
    ip_hash      TEXT NOT NULL,
    request_time TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rl_ip ON rate_limit_log(ip_hash, request_time DESC);

-- ── Chat history ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat_messages (
    id              SERIAL PRIMARY KEY,
    patient_id      TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant')),
    message_text    TEXT NOT NULL,
    answer_mode     TEXT,
    used_records    BOOLEAN DEFAULT FALSE,
    used_textbook   BOOLEAN DEFAULT FALSE,
    used_attachment BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_patient ON chat_messages(patient_id, created_at DESC);

-- ── Audit log ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id           SERIAL PRIMARY KEY,
    event_time   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type   TEXT NOT NULL,
    patient_id   TEXT,
    patient_hash TEXT,
    user_role    TEXT,
    action       TEXT NOT NULL,
    phi_detected BOOLEAN DEFAULT FALSE,
    phi_types    TEXT,
    query_hash   TEXT,
    outcome      TEXT,
    details      TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(event_time DESC);

-- ── Medical knowledge chunks (pgvector RAG) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS medical_chunks (
    id          SERIAL PRIMARY KEY,
    chunk_id    TEXT UNIQUE NOT NULL,
    source_file TEXT NOT NULL,
    page_number INT NOT NULL,
    chunk_index INT NOT NULL,
    chunk_text  TEXT NOT NULL,
    embedding   vector(768)
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON medical_chunks(source_file, page_number);
-- Run after loading data: CREATE INDEX idx_chunks_vec ON medical_chunks
--   USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ── Demo data ─────────────────────────────────────────────────────────────────
INSERT INTO patient_visits (patient_id,visit_date,visit_type,chief_complaint,clinical_notes,doctor_name)
VALUES
  ('demo-patient-001','2025-03-10','General Checkup','Fatigue and mild fever',
   'WBC elevated at 11.2. Likely viral illness. Rest and hydration advised.','Dr. Sharma'),
  ('demo-patient-001','2024-12-05','Follow-up','Diabetes management',
   'HbA1c 7.4%, improved from 7.9%. BP 128/82. Continuing current medications.','Dr. Sharma')
ON CONFLICT DO NOTHING;

INSERT INTO patient_labs (patient_id,test_date,test_name,test_value,unit,reference_range,is_abnormal,lab_name)
VALUES
  ('demo-patient-001','2025-03-10','WBC','11.2','x10³/µL','4.5–11.0',TRUE,'City Lab'),
  ('demo-patient-001','2025-03-10','Hemoglobin','13.8','g/dL','13.5–17.5',FALSE,'City Lab'),
  ('demo-patient-001','2024-12-05','HbA1c','7.4','%','<7.0',TRUE,'City Lab'),
  ('demo-patient-001','2024-08-05','HbA1c','7.9','%','<7.0',TRUE,'City Lab'),
  ('demo-patient-001','2024-12-05','Fasting Glucose','138','mg/dL','70–100',TRUE,'City Lab'),
  ('demo-patient-001','2024-12-05','Creatinine','0.9','mg/dL','0.7–1.3',FALSE,'City Lab')
ON CONFLICT DO NOTHING;

INSERT INTO patient_medications (patient_id,medication_name,dosage,frequency,start_date,is_active,prescribing_doctor,indication)
VALUES
  ('demo-patient-001','Metformin','500mg','Twice daily','2023-06-01',TRUE,'Dr. Sharma','Type 2 Diabetes'),
  ('demo-patient-001','Amlodipine','5mg','Once daily','2023-06-01',TRUE,'Dr. Sharma','Hypertension'),
  ('demo-patient-001','Aspirin','75mg','Once daily','2023-06-01',TRUE,'Dr. Sharma','Cardiovascular prevention')
ON CONFLICT DO NOTHING;

INSERT INTO patient_profiles (patient_id,full_name)
VALUES ('demo-patient-001','Demo User')
ON CONFLICT (patient_id) DO NOTHING;
