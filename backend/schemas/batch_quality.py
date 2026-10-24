from datetime import datetime
from pydantic import BaseModel


class BatchQualityOut(BaseModel):
    work_order_id: str
    batch_id: str
    machine_id: str
    quality_score: float
    risk_level: str
    probability_passing_qc: float
    confidence: float
    contributing_factors: list[str]
    sampling_rate_pct: float
    sampling_rationale: str
    ai_summary: str
    llm_generated: bool
    is_live: bool = False
    computed_at: datetime

    class Config:
        from_attributes = True
