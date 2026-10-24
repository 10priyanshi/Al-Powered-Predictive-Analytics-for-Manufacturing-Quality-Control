from sqlalchemy import Column, Integer, String, DateTime, Text, Enum as SAEnum
from sqlalchemy.sql import func
from backend.models.database import Base
from backend.models.enums import AlertSeverity, AlertStatus


class Alert(Base):
    """An alert raised by any agent when a threshold/event fires."""
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(String(64), unique=True, index=True, nullable=False)
    machine_id = Column(String(64), index=True, nullable=True)
    line_id = Column(String(32), index=True, nullable=True)
    severity = Column(SAEnum(AlertSeverity), nullable=False, index=True)
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=False)
    trigger_data_json = Column(Text, nullable=True)  # explanation payload from source agent
    source_agent = Column(String(64), nullable=False)
    related_defect_id = Column(String(64), nullable=True, index=True)
    related_event_id = Column(String(64), nullable=True, index=True)
    status = Column(SAEnum(AlertStatus), nullable=False, default=AlertStatus.UNREAD, index=True)
    acknowledged_by = Column(String(64), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
