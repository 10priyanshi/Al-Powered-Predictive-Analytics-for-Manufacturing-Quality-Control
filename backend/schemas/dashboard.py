from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class AuditLogOut(BaseModel):
    timestamp: datetime
    username: Optional[str]
    role: str
    action_type: str
    entity_type: str
    entity_id: str
    details: Optional[dict]
    outcome: Optional[str]

    class Config:
        from_attributes = True


class MachineStatusOut(BaseModel):
    machine_id: str
    machine_name: str
    line_id: str
    machine_type: str
    risk_score: float
    risk_level: str
    operational_status: str
    active_work_order_id: Optional[str] = None
    active_alert_count: int
    last_maintenance_date: Optional[datetime]


class ModelPerformanceOut(BaseModel):
    total_recommendations: int
    approved: int
    rejected: int
    modified: int
    pending: int
    approval_rate: Optional[float]
