from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.models.database import Base


class Line(Base):
    """A production line, e.g. LINE1, LINE2, LINE3."""
    __tablename__ = "lines"

    id = Column(Integer, primary_key=True, index=True)
    line_code = Column(String(32), unique=True, index=True, nullable=False)  # "LINE3"
    name = Column(String(128), nullable=False)
    plant_area = Column(String(64), nullable=False)

    machines = relationship("Machine", back_populates="line")


class Machine(Base):
    """
    A physical station/machine on a line, e.g. LINE3-OVEN-02.
    `units_per_minute` is the station's known takt rate, used by the
    Event-to-Unit Traceability Agent to compute affected-unit ranges.
    """
    __tablename__ = "machines"

    id = Column(Integer, primary_key=True, index=True)
    machine_id = Column(String(64), unique=True, index=True, nullable=False)  # "LINE3-OVEN-02"
    name = Column(String(128), nullable=False)
    machine_type = Column(String(64), nullable=False)  # e.g. "oven", "press", "weld_cell"
    line_id = Column(Integer, ForeignKey("lines.id"), nullable=False)
    install_date = Column(DateTime(timezone=True), nullable=True)
    last_maintenance_date = Column(DateTime(timezone=True), nullable=True)
    next_maintenance_due = Column(DateTime(timezone=True), nullable=True)
    units_per_minute = Column(Float, nullable=False, default=10.0)

    line = relationship("Line", back_populates="machines")
