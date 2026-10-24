from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class AlertOut(BaseModel):
    alert_id: str
    machine_id: Optional[str]
    line_id: Optional[str]
    severity: str
    title: str
    description: str
    trigger_data: Optional[dict]
    source_agent: str
    related_defect_id: Optional[str]
    related_event_id: Optional[str]
    status: str
    acknowledged_by: Optional[str]
    acknowledged_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class AlertSummaryOut(BaseModel):
    unread_count: int
    critical_count: int
    warning_count: int
