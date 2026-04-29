-- HealthNav INSTANT FIX — Run this in pgAdmin on your healthnav database NOW
-- This fixes the "Parsing... nothing extracted" stuck bug

-- Fix 1: Add missing columns (this is the primary crash cause)
ALTER TABLE patient_file_extractions ADD COLUMN IF NOT EXISTS visual_summary TEXT;
ALTER TABLE patient_file_extractions ADD COLUMN IF NOT EXISTS source_kind TEXT;

-- Fix 2: Reset any stuck files so you can re-parse them
UPDATE patient_files
SET parse_status = 'queued',
    parse_notes  = NULL,
    parse_report_type = NULL,
    parse_confidence  = NULL,
    labs_parsed  = 0,
    visits_parsed = 0,
    meds_parsed  = 0,
    parsed_at    = NULL
WHERE parse_status IN ('queued', 'parsing', 'analyzing')
  AND (parsed_at IS NULL OR parsed_at < NOW() - INTERVAL '5 minutes');

-- Check what's there
SELECT id, original_filename, parse_status, labs_parsed, parse_notes
FROM patient_files
ORDER BY uploaded_at DESC
LIMIT 10;
