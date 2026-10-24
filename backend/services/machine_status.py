"""
Machine operational status
=============================
Derives a real-time operational state for a machine from data that
already exists - no new "status" field to keep manually in sync:

  - RUNNING          - has a work order marked in_progress right now
  - MAINTENANCE_DUE   - no active job, and the machine is past its
                         next_maintenance_due date
  - IDLE              - no active job, not overdue for maintenance

This is deliberately a read-only derivation, not a stored field, so it
can never drift out of sync with the underlying work orders/maintenance
schedule the way a manually-toggled status flag could.
"""
from datetime import datetime
from sqlalchemy.orm import Session

from backend.models.machine import Machine
from backend.models.mes import WorkOrder
from backend.models.enums import WorkOrderStatus


def get_machine_operational_status(db: Session, machine: Machine, current_work_orders: dict[str, WorkOrder] | None = None) -> str:
    """
    `current_work_orders` is an optional pre-fetched map of
    machine_id -> in_progress WorkOrder, so a caller scoring many
    machines at once (e.g. the dashboard grid) can look this up with a
    single query instead of one query per machine.
    """
    if current_work_orders is not None:
        is_running = machine.machine_id in current_work_orders
    else:
        is_running = (
            db.query(WorkOrder)
            .filter(WorkOrder.machine_id == machine.machine_id, WorkOrder.status == WorkOrderStatus.IN_PROGRESS)
            .first()
            is not None
        )
    if is_running:
        return "running"

    if machine.next_maintenance_due and machine.next_maintenance_due < datetime.utcnow():
        return "maintenance_due"

    return "idle"


def get_running_work_orders_by_machine(db: Session) -> dict[str, WorkOrder]:
    """Single-query helper for bulk status lookups (the dashboard grid)."""
    rows = db.query(WorkOrder).filter(WorkOrder.status == WorkOrderStatus.IN_PROGRESS).all()
    return {wo.machine_id: wo for wo in rows}
