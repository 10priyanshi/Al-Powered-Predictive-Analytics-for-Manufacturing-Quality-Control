from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Enum as SAEnum, Index
from sqlalchemy.sql import func
from backend.models.database import Base
from backend.models.enums import SensorType, DataQualityFlag


class SensorReading(Base):
    """
    A single timestamped sensor reading. `is_anomalous` is set by the
    data-quality layer / Prediction Agent's threshold checks (not the
    generator) to flag statistically unusual values regardless of the
    scenario that produced them.
    """
    __tablename__ = "sensor_readings"

    id = Column(Integer, primary_key=True, index=True)
    machine_id = Column(String(64), index=True, nullable=False)
    sensor_type = Column(SAEnum(SensorType), nullable=False, index=True)
    value = Column(Float, nullable=True)  # nullable to represent MISSING readings
    unit = Column(String(16), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    is_anomalous = Column(Boolean, default=False, nullable=False)
    data_quality_flag = Column(SAEnum(DataQualityFlag), default=DataQualityFlag.OK, nullable=False)
    scenario_tag = Column(String(64), nullable=True)  # generator-only debug tag, e.g. "drift", "spike"

    __table_args__ = (
        Index("ix_sensor_machine_type_time", "machine_id", "sensor_type", "timestamp"),
    )
