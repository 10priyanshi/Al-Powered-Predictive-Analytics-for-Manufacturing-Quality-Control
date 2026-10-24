from sqlalchemy import Column, Integer, String, DateTime, Enum as SAEnum, Text
from backend.models.database import Base
from backend.models.enums import InspectionResult, DefectType


class Inspection(Base):
    """A quality inspection record tied to a batch/machine/station."""
    __tablename__ = "inspections"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String(64), index=True, nullable=False)
    machine_id = Column(String(64), index=True, nullable=False)
    station = Column(String(64), nullable=False)
    inspector = Column(String(128), nullable=False)
    result = Column(SAEnum(InspectionResult), nullable=False, index=True)
    defect_type = Column(SAEnum(DefectType), nullable=True)  # null when result == PASS
    defect_location = Column(String(128), nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    notes = Column(Text, nullable=True)
