from sqlalchemy import Column, Integer, String, DateTime, Enum as SAEnum, Text
from backend.models.database import Base
from backend.models.enums import MaintenanceType


class MaintenanceLog(Base):
    """A maintenance event performed on a machine."""
    __tablename__ = "maintenance_logs"

    id = Column(Integer, primary_key=True, index=True)
    machine_id = Column(String(64), index=True, nullable=False)
    maintenance_type = Column(SAEnum(MaintenanceType), nullable=False)
    performed_by = Column(String(128), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    notes = Column(Text, nullable=True)
    next_due_date = Column(DateTime(timezone=True), nullable=True)
