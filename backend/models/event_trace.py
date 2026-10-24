from sqlalchemy import Column, Integer, String, DateTime, Text, Enum as SAEnum
from sqlalchemy.sql import func
from backend.models.database import Base
from backend.models.enums import EventType, EventTraceStatus


class EventToUnitTrace(Base):
    """
    A physical, event-triggered anomaly (e.g. foreign-object detection)
    and the exact unit range it affects, computed deterministically
    from station takt time and the event's start/end timestamps.
    Distinct from the Prediction Agent's probabilistic risk scoring.
    """
    __tablename__ = "event_traces"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(64), unique=True, index=True, nullable=False)
    event_type = Column(SAEnum(EventType), nullable=False, index=True)
    station = Column(String(64), nullable=False)
    machine_id = Column(String(64), index=True, nullable=False)
    batch_id = Column(String(64), index=True, nullable=True)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    affected_unit_start = Column(Integer, nullable=False)
    affected_unit_end = Column(Integer, nullable=False)
    affected_unit_count = Column(Integer, nullable=False)
    recommended_action = Column(String(256), nullable=False)
    status = Column(SAEnum(EventTraceStatus), nullable=False, default=EventTraceStatus.OPEN, index=True)
    recurrence_count = Column(Integer, nullable=False, default=1)  # how many times this station has fired
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
