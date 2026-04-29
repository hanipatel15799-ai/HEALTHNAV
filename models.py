"""
models.py — HealthNav Pydantic models (single source of truth).

All request/response schemas live here.
api_models.py re-exports from this file for backwards compatibility.
"""
from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8, max_length=200)


class LoginResponse(BaseModel):
    ok: bool
    patient_id: str
    username: str
    role: str = "patient"


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    email: str = Field(..., min_length=5, max_length=200)
    full_name: str = Field(default="", max_length=200)
    password: str = Field(..., min_length=8, max_length=200)
    confirm_password: str = Field(..., min_length=8, max_length=200)

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class RegisterResponse(BaseModel):
    ok: bool
    username: str
    patient_id: str
    role: str


class MeResponse(BaseModel):
    ok: bool
    username: str
    patient_id: str
    role: str


# ── Lab Results ───────────────────────────────────────────────────────────────

class LabResultIn(BaseModel):
    """Request body for manually adding a lab result."""
    test_name: str = Field(..., min_length=1, max_length=300)
    test_date: str = Field(..., description="ISO date YYYY-MM-DD")
    test_value: str = Field(default="")
    unit: str = Field(default="")
    reference_range: str = Field(default="")
    is_abnormal: bool = Field(default=False)
    lab_name: str = Field(default="")


class LabResultOut(BaseModel):
    id: int
    patient_id: str
    test_date: str
    test_name: str
    test_value: Optional[str] = ""
    unit: Optional[str] = ""
    reference_range: Optional[str] = ""
    is_abnormal: bool = False
    lab_name: Optional[str] = ""


# ── Visits ────────────────────────────────────────────────────────────────────

class VisitIn(BaseModel):
    visit_date: str = Field(..., description="ISO date YYYY-MM-DD")
    visit_type: str = Field(default="General")
    chief_complaint: str = Field(default="")
    clinical_notes: str = Field(default="")
    doctor_name: str = Field(default="")


class VisitOut(BaseModel):
    id: int
    patient_id: str
    visit_date: str
    visit_type: Optional[str] = ""
    chief_complaint: Optional[str] = ""
    clinical_notes: Optional[str] = ""
    doctor_name: Optional[str] = ""


# ── Medications ───────────────────────────────────────────────────────────────

class MedicationIn(BaseModel):
    medication_name: str = Field(..., min_length=1, max_length=300)
    dosage: str = Field(default="")
    frequency: str = Field(default="")
    start_date: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="ISO date YYYY-MM-DD")
    prescribing_doctor: str = Field(default="")
    indication: str = Field(default="")
    is_active: bool = Field(default=True)


class MedicationOut(BaseModel):
    id: int
    patient_id: str
    medication_name: str
    dosage: Optional[str] = ""
    frequency: Optional[str] = ""
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    prescribing_doctor: Optional[str] = ""
    indication: Optional[str] = ""
    is_active: bool = True


# ── Chat ──────────────────────────────────────────────────────────────────────

class SourceUsage(BaseModel):
    used_records: bool = False
    used_textbook: bool = False
    used_attachment: bool = False


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    role: str = Field(default="patient", pattern="^(patient|clinician)$")


class ChatResponse(BaseModel):
    answer: str
    citations: List[str] = []
    phi_warning: bool = False
    blocked: bool = False
    block_reason: str = ""
    sources: Optional[SourceUsage] = None


# ── File / Upload ─────────────────────────────────────────────────────────────

class UploadResponse(BaseModel):
    ok: bool
    file_id: Optional[int] = None
    filename: str
    stored_filename: str
    category: str
    can_parse: bool
    message: str


class ParseStatusResponse(BaseModel):
    file_id: int
    parse_status: str
    report_type: Optional[str] = None
    confidence: Optional[str] = None
    parse_notes: Optional[str] = None
    labs_inserted: int = 0
    visits_inserted: int = 0
    meds_inserted: int = 0
    parsed_at: Optional[str] = None
    done: bool = False


# ── Summary ───────────────────────────────────────────────────────────────────

class SummaryResponse(BaseModel):
    total_labs: int = 0
    abnormal_count: int = 0
    total_visits: int = 0
    active_meds: int = 0
    uploaded_files: int = 0


# ── Health check ──────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    api: str
    db: str
    vertex: str
    details: Optional[str] = None


# ── Profile ───────────────────────────────────────────────────────────────────

class ProfileResponse(BaseModel):
    patient_id: str
    full_name: str = ""
    date_of_birth: Optional[str] = None
    sex: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    full_name: str = Field(default="", max_length=200)
    date_of_birth: Optional[str] = None
    sex: Optional[str] = Field(default=None, max_length=50)
