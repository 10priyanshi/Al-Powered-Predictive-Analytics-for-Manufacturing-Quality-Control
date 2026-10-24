from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field


class RecommendationOut(BaseModel):
    recommendation_id: str
    source_type: str
    source_id: str
    action_text: str
    reasoning: str
    confidence: float
    similar_case_ids: list[str]
    assigned_role: str
    status: str
    reviewed_by: Optional[str]
    reviewed_at: Optional[datetime]
    modification_note: Optional[str]
    llm_generated: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class ReviewRecommendationRequest(BaseModel):
    action: Literal["approve", "reject", "modify"]
    modification_note: Optional[str] = Field(
        default=None, description="Required when action == 'modify'; explains what was changed."
    )
