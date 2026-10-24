from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel


class GenerateReportRequest(BaseModel):
    report_type: Literal["shift", "daily"]
    period_start: datetime
    period_end: datetime


class ReportOut(BaseModel):
    report_id: str
    report_type: str
    period_start: datetime
    period_end: datetime
    generated_by: str
    content: dict
    status: str
    finalized_by: Optional[str]
    finalized_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class UpdateReportRequest(BaseModel):
    content: Optional[dict] = None
    finalize: bool = False
