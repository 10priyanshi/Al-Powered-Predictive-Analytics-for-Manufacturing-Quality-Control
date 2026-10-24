from sqlalchemy import Column, Integer, String, DateTime, Float, Enum as SAEnum
from backend.models.database import Base
from backend.models.enums import WorkOrderStatus


class MaterialLot(Base):
    """
    A supplier material lot. `quality_score` (0-100) is a synthetic
    running measure of how clean this lot's history has been, used by
    the Pre-Production Risk Agent as a batch-genealogy signal.
    """
    __tablename__ = "material_lots"

    id = Column(Integer, primary_key=True, index=True)
    lot_id = Column(String(64), unique=True, index=True, nullable=False)  # "SUP-22-0091"
    supplier_id = Column(String(32), index=True, nullable=False)
    supplier_name = Column(String(128), nullable=False)
    material_type = Column(String(64), nullable=False)
    received_date = Column(DateTime(timezone=True), nullable=False)
    quality_score = Column(Float, nullable=False, default=90.0)
    prior_contamination_defects = Column(Integer, nullable=False, default=0)


class WorkOrder(Base):
    """
    An MES work order / batch record. This is the shared genealogy
    record: batch_id, machine_id, material_lot_id, product_sku all tie
    together here so downstream agents can join across data types.
    """
    __tablename__ = "work_orders"

    id = Column(Integer, primary_key=True, index=True)
    work_order_id = Column(String(64), unique=True, index=True, nullable=False)
    batch_id = Column(String(64), unique=True, index=True, nullable=False)
    product_sku = Column(String(64), index=True, nullable=False)
    machine_id = Column(String(64), index=True, nullable=False)
    line_id = Column(String(32), index=True, nullable=False)
    material_lot_id = Column(String(64), index=True, nullable=False)
    planned_shift = Column(String(16), nullable=False)
    planned_start = Column(DateTime(timezone=True), nullable=False, index=True)
    planned_end = Column(DateTime(timezone=True), nullable=False)
    actual_start = Column(DateTime(timezone=True), nullable=True)
    actual_end = Column(DateTime(timezone=True), nullable=True)
    quantity_planned = Column(Integer, nullable=False)
    status = Column(SAEnum(WorkOrderStatus), nullable=False, default=WorkOrderStatus.SCHEDULED, index=True)
