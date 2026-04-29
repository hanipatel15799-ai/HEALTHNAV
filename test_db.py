from patient_record_retrieval import ensure_tables_exist, get_recent_visits

ensure_tables_exist()
print(get_recent_visits("demo-patient-001", limit=3))
