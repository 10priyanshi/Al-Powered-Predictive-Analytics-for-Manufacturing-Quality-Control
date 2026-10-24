from sqlalchemy import Column, Integer, String, DateTime, Boolean, Enum as SAEnum
from backend.models.database import Base
from backend.models.enums import ShiftName


class ProductionLog(Base):
    """
    A production run/segment log, linking a line+machine to the
    batch/work-order it produced during a shift. `unit_seq_start` /
    `unit_seq_end` give the batch-sequence numbering used by the
    Event-to-Unit Traceability Agent to map time windows to unit IDs.
    """
    __tablename__ = "production_logs"

    id = Column(Integer, primary_key=True, index=True)
    line_id = Column(String(32), index=True, nullable=False)
    machine_id = Column(String(64), index=True, nullable=False)
    batch_id = Column(String(64), index=True, nullable=False)
    work_order_id = Column(String(64), index=True, nullable=False)
    shift = Column(SAEnum(ShiftName), nullable=False)
    operator_username = Column(String(64), nullable=False)
    units_produced = Column(Integer, nullable=False, default=0)
    units_scrapped = Column(Integer, nullable=False, default=0)
    unit_seq_start = Column(Integer, nullable=False)  # first unit # produced in this segment
    unit_seq_end = Column(Integer, nullable=False)    # last unit # produced in this segment
    start_time = Column(DateTime(timezone=True), nullable=False, index=True)
    end_time = Column(DateTime(timezone=True), nullable=False)
    changeover_flag = Column(Boolean, default=False, nullable=False)
