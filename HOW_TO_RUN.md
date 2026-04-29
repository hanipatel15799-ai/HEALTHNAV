# HealthNav — How to run

## 1. Put the files in one folder
Suggested path on Windows:
`C:\healthnav`

## 2. Create `.env`
Copy `.env.example` to `.env` and fill your real values.

## 3. Create a virtual environment
```bash
py -3.11 -m venv venv
venv\Scriptsctivate
```

## 4. Install dependencies
```bash
pip install -r requirements.txt
```

## 5. Create patient tables
Run `create_patient_tables.sql` in pgAdmin Query Tool or with:
```bash
psql -U postgres -d healthnav -f create_patient_tables.sql
```

## 6. Test environment, DB, and Vertex
```bash
python test_env.py
python test_db.py
python test_vertex.py
```

## 7. Optional textbook retrieval build
Put PDFs into `data/medical_pdfs/`, then run:
```bash
python chunk_text.py
python load_chunks_to_db.py
```

## 8. Start the app
```bash
uvicorn main:app --reload --port 8000
```

## 9. Open the dashboard
- `http://localhost:8000`
- `http://localhost:8000/docs`

## 10. Login
Use the demo credentials from `.env`:
- username = DEMO_USERNAME
- password = DEMO_PASSWORD
