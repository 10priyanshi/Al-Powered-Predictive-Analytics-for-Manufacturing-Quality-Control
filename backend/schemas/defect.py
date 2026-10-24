from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class RootCauseOut(BaseModel):
    defect_id: str
    category_split: dict  # {"equipment": 0.6, "process": 0.4}
    primary_category: str
    contributing_factors: list[str]
    explanation: str
    confidence: float
    routed_role: str
    llm_generated: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class DefectOut(BaseModel):
    defect_id: str
    batch_id: Optional[str]
    machine_id: str
    work_order_id: Optional[str]
    defect_type: str
    location: str
    detected_at: datetime
    source: str
    risk_score: Optional[float]
    status: str
    root_cause: Optional[RootCauseOut] = None

    class Config:
        from_attributes = True


class FlagDefectRequest(BaseModel):
    batch_id: Optional[str] = None
    machine_id: str
    defect_type: str
    location: str
    notes: Optional[str] = None
