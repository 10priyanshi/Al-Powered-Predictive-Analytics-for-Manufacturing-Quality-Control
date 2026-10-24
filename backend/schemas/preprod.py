from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class PreProdRiskOut(BaseModel):
    work_order_id: str
    risk_score: float
    contributing_reasons: list[str]
    suggested_action: str
    confidence: float
    status: str
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    is_holdout: bool
    actual_outcome: Optional[bool]
    created_at: datetime

    class Config:
        from_attributes = True


class ValidationMetricsOut(BaseModel):
    holdout_size: int
    precision: Optional[float]
    recall: Optional[float]
    f1_score: Optional[float]
    threshold_used: float
    note: str


class EventTraceOut(BaseModel):
    event_id: str
    event_type: str
    station: str
    machine_id: str
    batch_id: Optional[str]
    start_time: datetime
    end_time: datetime
    affected_unit_start: int
    affected_unit_end: int
    affected_unit_count: int
    recommended_action: str
    status: str
    recurrence_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class EventTraceDecisionRequest(BaseModel):
    decision: str  # "quarantined" | "released" | "escalated"
