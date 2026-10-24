from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class RiskFactorOut(BaseModel):
    factor: str
    detail: str


class RiskScoreOut(BaseModel):
    machine_id: str
    line_id: str
    risk_score: float  # 0-100
    risk_level: str  # "low" | "medium" | "high"
    contributing_factors: list[str]
    confidence: float  # 0-1
    based_on: str  # short explanation of what data this used
    computed_at: datetime
