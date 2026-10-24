"""
Centralized audit logging. Every prediction, alert, recommendation, and
human decision passes through here so the audit trail is consistent and
records the acting role, not just a user id, per the spec's requirement.
"""
import json
from datetime import datetime, date
from typing import Optional
from sqlalchemy.orm import Session
from backend.models.audit import AuditLog


def _json_default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


def log_action(
    db: Session,
    role: str,
    action_type: str,
    entity_type: str,
    entity_id: str,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    details: Optional[dict] = None,
    outcome: Optional[str] = None,
    commit: bool = True,
) -> AuditLog:
    entry = AuditLog(
        user_id=user_id,
        username=username,
        role=role,
        action_type=action_type,
        entity_type=entity_type,
        entity_id=entity_id,
        details_json=json.dumps(details, default=_json_default) if details is not None else None,
        outcome=outcome,
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry
