from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FAQCreate(BaseModel):
    question: str
    answer: str
    tags: list[str] = []
    vectorize: bool = True


class FAQVectorizeResult(BaseModel):
    faq_id: int
    success: bool
    error: str = ""


class FAQBackfillResult(BaseModel):
    total: int
    success: int
    errors: list[str]


class FAQUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    tags: Optional[list[str]] = None
    is_active: Optional[bool] = None


class FAQResponse(BaseModel):
    id: int
    tenant_id: int
    question: str
    answer: str
    tags: list[str]
    is_active: bool
    embedding: Optional[list[float]] = None
    created_at: datetime
    updated_at: datetime


class FAQMatchRequest(BaseModel):
    question: str
    direct_threshold: float = 0.85


class FAQMatchResponse(BaseModel):
    matched: bool
    faq: Optional[FAQResponse] = None
    score: float = 0.0
    tier: str = ""  # "direct" | "rag" | "none"


class FAQRagRequest(BaseModel):
    question: str
    agent_id: str = "faq"


class FAQRagResponse(BaseModel):
    answer: str
    sources: list[dict] = []


class FAQImportRow(BaseModel):
    question: str
    answer: str
    tags: str = ""  # comma-separated


class FAQImportResult(BaseModel):
    total: int
    success: int
    errors: list[str]
