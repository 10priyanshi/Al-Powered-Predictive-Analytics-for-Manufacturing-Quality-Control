from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Enum as SAEnum, Boolean
from sqlalchemy.sql import func
from backend.models.database import Base
from backend.models.enums import RecommendationStatus


class Recommendation(Base):
    """
    A corrective-action recommendation from the RAC (Recommendation)
    Agent, or a pre-emptive action from the Pre-Production Risk Agent
    (source_type distinguishes these). Always requires human
    Approve/Reject/Modify — never auto-applied.
    """
    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, index=True)
    recommendation_id = Column(String(64), unique=True, index=True, nullable=False)
    source_type = Column(String(32), nullable=False, index=True)  # "defect" | "preprod_batch" | "event_trace"
    source_id = Column(String(64), index=True, nullable=False)  # defect_id / work_order_id / event_id
    action_text = Column(Text, nullable=False)
    reasoning = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False)  # 0-1
    similar_case_ids_json = Column(Text, nullable=True)  # list of KB case_ids used as precedent
    assigned_role = Column(String(32), nullable=False, index=True)
    status = Column(SAEnum(RecommendationStatus), nullable=False, default=RecommendationStatus.PENDING, index=True)
    reviewed_by = Column(String(64), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    modification_note = Column(Text, nullable=True)
    llm_generated = Column(Boolean, nullable=False, default=False)  # True if action_text/reasoning were written by Claude
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
