from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.machine import Machine
from backend.models.event_trace import EventToUnitTrace
from backend.models.enums import EventType, EventTraceStatus
from backend.auth.dependencies import get_current_user, CurrentUser
from backend.schemas.ingest import TriggerEventRequest
from backend.schemas.preprod import EventTraceOut, EventTraceDecisionRequest
from backend.agents import orchestrator
from backend.services.rbac_scope import line_scope_for_user
from backend.services.audit_service import log_action

router = APIRouter(prefix="/api/events", tags=["events"])


def _to_out(t: EventToUnitTrace) -> EventTraceOut:
    return EventTraceOut(
        event_id=t.event_id, event_type=t.event_type.value, station=t.station, machine_id=t.machine_id,
        batch_id=t.batch_id, start_time=t.start_time, end_time=t.end_time,
        affected_unit_start=t.affected_unit_start, affected_unit_end=t.affected_unit_end,
        affected_unit_count=t.affected_unit_count, recommended_action=t.recommended_action,
        status=t.status.value, recurrence_count=t.recurrence_count, created_at=t.created_at,
    )


@router.get("", response_model=list[EventTraceOut])
def list_events(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(EventToUnitTrace).order_by(EventToUnitTrace.created_at.desc())
    allowed_line = line_scope_for_user(current_user)
    if allowed_line:
        query = query.filter(EventToUnitTrace.machine_id.like(f"{allowed_line}-%"))
    return [_to_out(t) for t in query.limit(100).all()]


@router.post("/trigger", response_model=EventTraceOut)
def trigger_event(
    payload: TriggerEventRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Simulates a foreign-object/scrap-debris detection firing right now,
    so the demo can show the quarantine list populate live. In a real
    deployment this would be called by the vision-camera/sensor
    integration instead of a human.
    """
    machine = db.query(Machine).filter(Machine.machine_id == payload.machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail=f"Machine {payload.machine_id} not found.")
    allowed_line = line_scope_for_user(current_user)
    if allowed_line and not payload.machine_id.startswith(f"{allowed_line}-"):
        raise HTTPException(status_code=403, detail=f"Your role is scoped to {allowed_line} only.")

    end_time = datetime.utcnow()
    start_time = end_time - timedelta(seconds=payload.duration_seconds)
    result = orchestrator.handle_event(
        db, machine, EventType(payload.event_type), start_time, end_time, payload.batch_id
    )
    log_action(
        db, role=current_user.role.value, action_type="trigger_event", entity_type="event_trace",
        entity_id=result["trace"].event_id, user_id=current_user.id, username=current_user.username,
        outcome="success",
    )
    return _to_out(result["trace"])


@router.post("/{event_id}/decision", response_model=EventTraceOut)
def record_decision(
    event_id: str,
    payload: EventTraceDecisionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Quality Manager (or plant-wide roles) records the quarantine/
    release decision for the affected unit range."""
    trace = db.query(EventToUnitTrace).filter(EventToUnitTrace.event_id == event_id).first()
    if not trace:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found.")
    try:
        new_status = EventTraceStatus(payload.decision)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid decision '{payload.decision}'.")
    trace.status = new_status
    db.commit()
    db.refresh(trace)
    log_action(
        db, role=current_user.role.value, action_type="event_decision", entity_type="event_trace",
        entity_id=event_id, user_id=current_user.id, username=current_user.username, outcome=new_status.value,
    )
    return _to_out(trace)
