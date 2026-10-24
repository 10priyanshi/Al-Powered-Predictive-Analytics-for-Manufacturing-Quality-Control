from sqlalchemy import Column, Integer, String, DateTime, Text, Enum as SAEnum
from sqlalchemy.sql import func
from backend.models.database import Base
from backend.models.enums import ReportType, ReportStatus


class Report(Base):
    """A generated shift/daily quality report, editable before finalization."""
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(String(64), unique=True, index=True, nullable=False)
    report_type = Column(SAEnum(ReportType), nullable=False, index=True)
    period_start = Column(DateTime(timezone=True), nullable=False)
    period_end = Column(DateTime(timezone=True), nullable=False)
    generated_by = Column(String(64), nullable=False)
    content_json = Column(Text, nullable=False)  # structured report body
    status = Column(SAEnum(ReportStatus), nullable=False, default=ReportStatus.DRAFT, index=True)
    finalized_by = Column(String(64), nullable=True)
    finalized_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
