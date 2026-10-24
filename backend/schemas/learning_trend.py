from datetime import datetime
from pydantic import BaseModel


class LearningTrendPointOut(BaseModel):
    week_label: str
    week_number: int
    period_start: datetime
    period_end: datetime
    recommendation_accuracy_pct: float
    total_recommendations: int
    is_historical_seed: bool


class LearningTrendOut(BaseModel):
    points: list[LearningTrendPointOut]
    note: str
