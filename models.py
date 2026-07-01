from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class LeadSource(str, Enum):
    TELEGRAM = "tg"
    REDDIT = "reddit"
    GOOGLE = "google"
    VK = "vk"
    X = "x"
    XHS = "xhs"
    BOARDS = "boards"
    NAVER = "naver"
    HABR = "habr"
    BEHANCE = "behance"


class AIStatus(str, Enum):
    PENDING = "pending"
    QUALIFIED = "qualified"
    REJECTED = "rejected"


class RawPost(BaseModel):
    external_id: str
    source: LeadSource
    text: str
    author: str
    contact: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AIQualificationResult(BaseModel):
    is_lead: bool
    reason: str
    summary: Optional[str] = None


class LeadRecord(BaseModel):
    id: Optional[int] = None
    external_id: str
    source: LeadSource
    text: str
    author: str
    contact: Optional[str] = None
    timestamp: datetime
    ai_status: AIStatus = AIStatus.PENDING
    reason: Optional[str] = None
    summary: Optional[str] = None


class DiscoveredChat(BaseModel):
    id: Optional[int] = None
    username: str
    keyword: Optional[str] = None
    added_at: datetime
