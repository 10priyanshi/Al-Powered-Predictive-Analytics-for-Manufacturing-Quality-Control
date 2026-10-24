from sqlalchemy import Column, Integer, String, Float, DateTime, Enum as SAEnum
from sqlalchemy.sql import func
from backend.models.database import Base
from backend.models.enums import DefectType, DefectSource, DefectStatus


class Defect(Base):
    """
    A defect record — either predicted by the Prediction Agent, logged
    from a failed Inspection, or raised by the Event-to-Unit Agent.
    This is the anchor entity that RootCauseAnalysis, Recommendation,
    and Alert records attach to.
    """
    __tablename__ = "defects"

    id = Column(Integer, primary_key=True, index=True)
    defect_id = Column(String(64), unique=True, index=True, nullable=False)  # "DEF-000123"
    batch_id = Column(String(64), index=True, nullable=True)
    machine_id = Column(String(64), index=True, nullable=False)
    work_order_id = Column(String(64), index=True, nullable=True)
    defect_type = Column(SAEnum(DefectType), nullable=False, index=True)
    location = Column(String(128), nullable=False)  # station/process step
    detected_at = Column(DateTime(timezone=True), nullable=False, index=True)
    source = Column(SAEnum(DefectSource), nullable=False)
    risk_score = Column(Float, nullable=True)  # populated when source == PREDICTION
    status = Column(SAEnum(DefectStatus), nullable=False, default=DefectStatus.OPEN, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
