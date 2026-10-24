from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean
from sqlalchemy.sql import func
from backend.models.database import Base


class BatchQualityScore(Base):
    """
    AI Batch Quality Score - the primary QA-manager-facing summary for a
    batch, combining sensor data, machine health, operator history,
    material quality, and environmental conditions into one number.
    Distinct from (and built on top of) the underlying Prediction Agent's
    per-machine risk scoring, which remains the signal that drives Alerts
    and Defect creation - this is a higher-level composite view.
    """
    __tablename__ = "batch_quality_scores"

    id = Column(Integer, primary_key=True, index=True)
    work_order_id = Column(String(64), unique=True, index=True, nullable=False)
    batch_id = Column(String(64), index=True, nullable=False)
    machine_id = Column(String(64), index=True, nullable=False)
    quality_score = Column(Float, nullable=False)  # 0-100, higher = better
    risk_level = Column(String(16), nullable=False, index=True)  # low / medium / high
    probability_passing_qc = Column(Float, nullable=False)  # 0-100
    confidence = Column(Float, nullable=False)  # 0-1
    contributing_factors_json = Column(Text, nullable=False)
    sampling_rate_pct = Column(Float, nullable=False, default=20.0)  # AI-recommended inspection intensity, 0-100
    sampling_rationale = Column(Text, nullable=True)
    ai_summary = Column(Text, nullable=True)
    llm_generated = Column(Boolean, nullable=False, default=False)
    computed_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
