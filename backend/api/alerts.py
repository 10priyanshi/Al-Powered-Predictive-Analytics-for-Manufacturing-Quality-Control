import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.alert import Alert
from backend.models.enums import AlertStatus
from backend.auth.dependencies import get_current_user, CurrentUser
from backend.schemas.alert import AlertOut, AlertSummaryOut
from backend.services.rbac_scope import line_scope_for_user
from backend.services.audit_service import log_action

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _to_out(a: Alert) -> AlertOut:
    return AlertOut(
        alert_id=a.alert_id, machine_id=a.machine_id, line_id=a.line_id, severity=a.severity.value,
        title=a.title, description=a.description,
        trigger_data=json.loads(a.trigger_data_json) if a.trigger_data_json else None,
        source_agent=a.source_agent, related_defect_id=a.related_defect_id, related_event_id=a.related_event_id,
        status=a.status.value, acknowledged_by=a.acknowledged_by, acknowledged_at=a.acknowledged_at,
        created_at=a.created_at,
    )


def _scoped_query(db: Session, current_user: CurrentUser):
    query = db.query(Alert).order_by(Alert.created_at.desc())
    allowed_line = line_scope_for_user(current_user)
    if allowed_line:
        query = query.filter(Alert.line_id == allowed_line)
    return query


@router.get("", response_model=list[AlertOut])
def list_alerts(
    status_filter: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = _scoped_query(db, current_user)
    if status_filter:
        query = query.filter(Alert.status == AlertStatus(status_filter))
    return [_to_out(a) for a in query.limit(200).all()]


@router.get("/summary", response_model=AlertSummaryOut)
def alert_summary(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    query = _scoped_query(db, current_user)
    unread = query.filter(Alert.status == AlertStatus.UNREAD).count()
    critical = query.filter(Alert.severity == "critical", Alert.status != AlertStatus.ACKNOWLEDGED).count()
    warning = query.filter(Alert.severity == "warning", Alert.status != AlertStatus.ACKNOWLEDGED).count()
    return AlertSummaryOut(unread_count=unread, critical_count=critical, warning_count=warning)


@router.post("/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge_alert(alert_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    alert = db.query(Alert).filter(Alert.alert_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")
    allowed_line = line_scope_for_user(current_user)
    if allowed_line and alert.line_id != allowed_line:
        raise HTTPException(status_code=403, detail=f"Your role is scoped to {allowed_line} only.")
    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_by = current_user.username
    alert.acknowledged_at = datetime.utcnow()
    db.commit()
    db.refresh(alert)
    log_action(
        db, role=current_user.role.value, action_type="acknowledge_alert", entity_type="alert",
        entity_id=alert.alert_id, user_id=current_user.id, username=current_user.username, outcome="success",
    )
    return _to_out(alert)


@router.post("/{alert_id}/read", response_model=AlertOut)
def mark_alert_read(alert_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    alert = db.query(Alert).filter(Alert.alert_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")
    if alert.status == AlertStatus.UNREAD:
        alert.status = AlertStatus.READ
        db.commit()
        db.refresh(alert)
    return _to_out(alert)
