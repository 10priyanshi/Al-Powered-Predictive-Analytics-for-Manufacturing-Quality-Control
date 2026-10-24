"""
Event-to-Unit Traceability Agent
===================================
Handles physical, event-triggered anomalies (foreign-object/scrap
detection) as distinct from the Prediction Agent's continuous,
probabilistic risk scoring. This is deterministic physics/timing math:

    affected_unit_count = event_duration_minutes * units_per_minute
                           + buffer_units_each_side * 2

The unit range is then mapped onto the production log's actual
unit-sequence numbering (the same batch-genealogy sequencing MES
already tracks) rather than left as an abstract count, producing an
exact, traceable quarantine list.
"""
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from backend.models.machine import Machine
from backend.models.production import ProductionLog
from backend.models.event_trace import EventToUnitTrace
from backend.models.enums import EventType, EventTraceStatus

BUFFER_UNITS_EACH_SIDE = 1
RECURRENCE_WINDOW_DAYS = 7


def _find_covering_production_log(db: Session, machine_id: str, start_time, end_time) -> Optional[ProductionLog]:
    """Finds the production segment whose time range contains the event
    window, or the closest segment if none exactly contains it (handles
    events that straddle a segment boundary in the synthetic data)."""
    covering = (
        db.query(ProductionLog)
        .filter(
            ProductionLog.machine_id == machine_id,
            ProductionLog.start_time <= start_time,
            ProductionLog.end_time >= end_time,
        )
        .first()
    )
    if covering:
        return covering
    # fall back to the segment whose start_time is closest to the event
    return (
        db.query(ProductionLog)
        .filter(ProductionLog.machine_id == machine_id)
        .order_by(ProductionLog.start_time.desc())
        .first()
    )


def compute_affected_units(db: Session, machine: Machine, start_time, end_time) -> dict:
    """
    Pure calculation step, kept separate from DB writes so it can be
    unit-tested / reused without side effects.
    """
    duration_seconds = max(0.0, (end_time - start_time).total_seconds())
    duration_minutes = duration_seconds / 60.0
    raw_count = duration_minutes * machine.units_per_minute
    affected_count = max(1, round(raw_count) + BUFFER_UNITS_EACH_SIDE * 2)

    log = _find_covering_production_log(db, machine.machine_id, start_time, end_time)
    if log is None:
        # No production history for this machine - report count only, no unit IDs.
        return {
            "affected_unit_count": affected_count, "affected_unit_start": None,
            "affected_unit_end": None, "batch_id": None, "duration_seconds": duration_seconds,
        }

    elapsed_minutes = max(0.0, (start_time - log.start_time).total_seconds() / 60.0)
    estimated_unit_at_start = log.unit_seq_start + round(elapsed_minutes * machine.units_per_minute)
    # Clamp the estimate to the segment's actual unit range FIRST - the
    # event timestamp (e.g. "right now" for a live-triggered demo event)
    # can fall well outside the historical production segment's actual
    # time window, which would otherwise push the estimate far beyond
    # the segment and invert start/end after the later min() clamp.
    estimated_unit_at_start = max(log.unit_seq_start, min(log.unit_seq_end, estimated_unit_at_start))
    unit_start = max(log.unit_seq_start, estimated_unit_at_start - BUFFER_UNITS_EACH_SIDE)
    unit_end = min(log.unit_seq_end, unit_start + affected_count - 1)
    unit_end = max(unit_start, unit_end)  # never let end fall below start
    actual_count = unit_end - unit_start + 1

    return {
        "affected_unit_count": actual_count, "affected_unit_start": unit_start,
        "affected_unit_end": unit_end, "batch_id": log.batch_id, "duration_seconds": duration_seconds,
    }


def _recurrence_count(db: Session, machine_id: str, event_type: EventType, at_time) -> int:
    window_start = at_time - timedelta(days=RECURRENCE_WINDOW_DAYS)
    prior = (
        db.query(EventToUnitTrace)
        .filter(
            EventToUnitTrace.machine_id == machine_id,
            EventToUnitTrace.event_type == event_type,
            EventToUnitTrace.start_time >= window_start,
            EventToUnitTrace.start_time < at_time,
        )
        .count()
    )
    return prior + 1


def process_event(db: Session, machine: Machine, event_type: EventType, start_time, end_time, event_id: str) -> dict:
    calc = compute_affected_units(db, machine, start_time, end_time)
    recurrence = _recurrence_count(db, machine.machine_id, event_type, start_time)

    if calc["affected_unit_start"] is not None:
        recommended_action = (
            f"Quarantine units #{calc['affected_unit_start']}-#{calc['affected_unit_end']} "
            f"({calc['affected_unit_count']} units) for 100% inspection."
        )
    else:
        recommended_action = (
            f"No production log available to map exact unit IDs; quarantine the most recent "
            f"~{calc['affected_unit_count']} units produced on {machine.machine_id} for 100% inspection."
        )

    return {
        "event_id": event_id,
        "event_type": event_type.value,
        "station": machine.machine_id,
        "machine_id": machine.machine_id,
        "batch_id": calc["batch_id"],
        "start_time": start_time,
        "end_time": end_time,
        "affected_unit_start": calc["affected_unit_start"] or 0,
        "affected_unit_end": calc["affected_unit_end"] or 0,
        "affected_unit_count": calc["affected_unit_count"],
        "recommended_action": recommended_action,
        "recurrence_count": recurrence,
        "is_recurring_issue": recurrence >= 3,
    }
