HealthNav : AI-Powered Health Intelligence Platform

Overview

HealthNav is a patient-facing AI system that transforms medical data into clear, safe, and personalized health insights.

It integrates:
- Medical documents (PDFs, scans, reports)  
- Longitudinal patient records  
- Medical textbook knowledge (RAG)  
- AI reasoning (AWS Bedrock)  

Goal:
Make healthcare data understandable, actionable, and patient-centric.

---

Architecture


User (Frontend UI)
↓
FastAPI Backend (main.py)
↓
────────────────────────────────────

Core AI Pipeline
answer_with_ai.py

→ Patient DB (PostgreSQL)
→ File Pipeline (S3 + Parser)
→ RAG (pgvector embeddings)
→ AWS Bedrock (Claude)
────────────────────────────────────
    ↓

Safe, contextual health response


---

     System Design

File Processing Pipeline

Upload → S3 → DB Record → Parser → Structured Data


Supports:
- PDFs  
- Images (OCR via Tesseract)  
- CSV / text  

Extracts:
- Lab results  
- Medications  
- Clinical visits  

---

AI Reasoning Pipeline

User Query
↓
Patient Context Retrieval
↓
(Optional) RAG (Medical Textbooks)
↓
AWS Bedrock (Claude)
↓
Safe, grounded response


---

Security & Safety

- PHI detection + redaction  
- No diagnosis / no prescriptions  
- Audit logging (partial)  
- Session-based authentication  
- Patient-level data isolation  

---
Tech Stack

| Layer     | Technology                     |
|----------|------------------------------|
| Backend  | FastAPI                       |
| Database | PostgreSQL + pgvector         |
| AI       | AWS Bedrock (Claude)          |
| Storage  | AWS S3                        |
| OCR      | Tesseract                     |
| Auth     | Secure sessions               |

Quick Start (Local)

1. Setup

python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
2. Configure .env
APP_SECRET=your_secret

DB_NAME=healthnav
DB_USER=postgres
DB_PASSWORD=your_password
DB_HOST=localhost
DB_PORT=5432

AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
AWS_REGION=us-east-1

BEDROCK_MODEL=anthropic.claude-3-haiku-20240307-v1:0

S3_BUCKET=healthnav-patient-files
3. Database
psql -U postgres -c "CREATE DATABASE healthnav;"
psql -U postgres -d healthnav -f db/schema.sql
4. Run
uvicorn main:app --reload --port 8000

Open: http://localhost:8000 


AWS Deployment
Required Services
AWS Bedrock (Claude models)
S3 (file storage)
EC2 (backend hosting)
(Optional) RDS (PostgreSQL)

Steps
Enable Bedrock model access
Create S3 bucket
Deploy app to EC2
Configure .env

Run:
uvicorn main:app --host 0.0.0.0 --port 8000 

Features
AI-powered health explanations
Multi-user patient system
File upload + parsing pipeline
Lab trend analysis
RAG over medical textbooks
Safe-response guardrails
Chat history persistence

Known Improvements (In Progress)
Full HIPAA-compliant architecture
RBAC enforcement
Monitoring & observability
Frontend analytics dashboard

Example Use Cases
“Explain my lab results”
“Why is my cholesterol high?”
“Summarize my medical history”
“What changed in my reports over time?”

What Makes This Different
Unlike generic chatbots, HealthNav:
Uses actual patient data
Incorporates clinical knowledge (RAG)
Maintains longitudinal context
Enforces medical safety boundaries

Author
Hani Patel
NYU — MS Management & Systems
Background: MBBS → HealthTech / AI

Project Status
Production-capable backend
Moving toward HIPAA-grade deployment
