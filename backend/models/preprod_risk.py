from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, Enum as SAEnum
from sqlalchemy.sql import func
from backend.models.database import Base
from backend.models.enums import RecommendationStatus


class PreProductionRiskScore(Base):
    """
    Output of the Pre-Production Risk Agent for a scheduled (not-yet-run)
    work order. `is_holdout` marks records excluded from the rules that
    generated defect labels, so precision/recall can be evaluated
    honestly on a held-out set rather than the agent re-deriving its
    own generator logic. `actual_outcome` is filled in once the
    generator later decides whether that batch actually produced a
    defect, enabling the validation report.
    """
    __tablename__ = "preprod_risk_scores"

    id = Column(Integer, primary_key=True, index=True)
    work_order_id = Column(String(64), unique=True, index=True, nullable=False)
    risk_score = Column(Float, nullable=False)  # 0-100
    contributing_reasons_json = Column(Text, nullable=False)
    suggested_action = Column(String(256), nullable=False)
    confidence = Column(Float, nullable=False)
    status = Column(SAEnum(RecommendationStatus), nullable=False, default=RecommendationStatus.PENDING, index=True)
    reviewed_by = Column(String(64), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    is_holdout = Column(Boolean, default=False, nullable=False, index=True)
    actual_outcome = Column(Boolean, nullable=True)  # True = defect occurred, filled in after the fact
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
