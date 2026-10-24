import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.defect import Defect
from backend.models.root_cause import RootCauseAnalysis
from backend.models.enums import DefectSource, DefectStatus, DefectType, UserRole
from backend.auth.dependencies import get_current_user, CurrentUser
from backend.schemas.defect import DefectOut, RootCauseOut, FlagDefectRequest
from backend.services.rbac_scope import category_scope_for_defects, line_scope_for_user
from backend.services.audit_service import log_action
from backend.services.id_gen import next_id
from backend.agents.orchestrator import run_rca_and_recommendation

router = APIRouter(prefix="/api/defects", tags=["defects"])


def _to_defect_out(db: Session, defect: Defect) -> DefectOut:
    rca_row = db.query(RootCauseAnalysis).filter(RootCauseAnalysis.defect_id == defect.defect_id).first()
    rca_out = None
    if rca_row:
        rca_out = RootCauseOut(
            defect_id=rca_row.defect_id, category_split=json.loads(rca_row.category_split_json),
            primary_category=rca_row.primary_category, contributing_factors=json.loads(rca_row.contributing_factors_json),
            explanation=rca_row.explanation, confidence=rca_row.confidence, routed_role=rca_row.routed_role,
            llm_generated=rca_row.llm_generated, created_at=rca_row.created_at,
        )
    return DefectOut(
        defect_id=defect.defect_id, batch_id=defect.batch_id, machine_id=defect.machine_id,
        work_order_id=defect.work_order_id, defect_type=defect.defect_type.value, location=defect.location,
        detected_at=defect.detected_at, source=defect.source.value, risk_score=defect.risk_score,
        status=defect.status.value, root_cause=rca_out,
    )


@router.get("", response_model=list[DefectOut])
def list_defects(
    status_filter: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Defect).order_by(Defect.detected_at.desc())
    allowed_line = line_scope_for_user(current_user)
    if allowed_line:
        query = query.filter(Defect.machine_id.like(f"{allowed_line}-%"))
    if status_filter:
        query = query.filter(Defect.status == DefectStatus(status_filter))
    defects = query.limit(200).all()

    allowed_categories = category_scope_for_defects(current_user)
    if allowed_categories is not None:
        defect_ids = [d.defect_id for d in defects]
        rcas = db.query(RootCauseAnalysis).filter(RootCauseAnalysis.defect_id.in_(defect_ids)).all() if defect_ids else []
        rca_by_defect = {r.defect_id: r for r in rcas}
        defects = [
            d for d in defects
            if d.defect_id in rca_by_defect and rca_by_defect[d.defect_id].primary_category in allowed_categories
        ]
    return [_to_defect_out(db, d) for d in defects]


@router.get("/{defect_id}", response_model=DefectOut)
def get_defect(defect_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    defect = db.query(Defect).filter(Defect.defect_id == defect_id).first()
    if not defect:
        raise HTTPException(status_code=404, detail=f"Defect {defect_id} not found.")
    allowed_line = line_scope_for_user(current_user)
    if allowed_line and not defect.machine_id.startswith(f"{allowed_line}-"):
        raise HTTPException(status_code=403, detail="This defect is outside your role's scope.")
    return _to_defect_out(db, defect)


@router.post("/flag", response_model=DefectOut)
def flag_defect_manually(
    payload: FlagDefectRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Operators (and any role) can manually flag a defect they observed
    on the floor, independent of the automated prediction/inspection paths."""
    try:
        defect_type = DefectType(payload.defect_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown defect_type '{payload.defect_type}'.")

    allowed_line = line_scope_for_user(current_user)
    if allowed_line and not payload.machine_id.startswith(f"{allowed_line}-"):
        raise HTTPException(status_code=403, detail=f"Your role is scoped to {allowed_line} only.")

    defect_id = next_id(db, Defect, "defect_id", "DEF", 300000)
    defect = Defect(
        defect_id=defect_id, batch_id=payload.batch_id, machine_id=payload.machine_id, work_order_id=None,
        defect_type=defect_type, location=payload.location, detected_at=datetime.utcnow(),
        source=DefectSource.MANUAL, risk_score=None, status=DefectStatus.OPEN,
    )
    db.add(defect)
    db.commit()
    db.refresh(defect)

    log_action(
        db, role=current_user.role.value, action_type="flag_defect", entity_type="defect",
        entity_id=defect.defect_id, user_id=current_user.id, username=current_user.username,
        details={"notes": payload.notes}, outcome="success",
    )

    run_rca_and_recommendation(db, defect)
    return _to_defect_out(db, defect)
