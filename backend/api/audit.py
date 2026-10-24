import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.audit import AuditLog
from backend.auth.dependencies import require_roles, CurrentUser, ROLES_PLANT_WIDE_VIEW
from backend.schemas.dashboard import AuditLogOut

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", response_model=list[AuditLogOut])
def list_audit_log(
    role_filter: str | None = None,
    action_type: str | None = None,
    entity_type: str | None = None,
    limit: int = 200,
    current_user: CurrentUser = Depends(require_roles(*ROLES_PLANT_WIDE_VIEW)),
    db: Session = Depends(get_db),
):
    """Full audit trail - who (by role, not just user id) did what to
    which entity and when. Restricted to Quality Manager, Plant Head,
    and Admin so behavior can be reviewed by role across the plant."""
    query = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
    if role_filter:
        query = query.filter(AuditLog.role == role_filter)
    if action_type:
        query = query.filter(AuditLog.action_type == action_type)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    rows = query.limit(min(limit, 1000)).all()
    return [
        AuditLogOut(
            timestamp=r.timestamp, username=r.username, role=r.role, action_type=r.action_type,
            entity_type=r.entity_type, entity_id=r.entity_id,
            details=json.loads(r.details_json) if r.details_json else None, outcome=r.outcome,
        )
        for r in rows
    ]
