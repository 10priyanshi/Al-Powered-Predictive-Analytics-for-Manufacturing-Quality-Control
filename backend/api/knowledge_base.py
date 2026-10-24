from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.knowledge_base import SimilarPastCase
from backend.models.enums import UserRole
from backend.auth.dependencies import get_current_user, require_roles, CurrentUser
from backend.services.id_gen import next_id
from backend.services.audit_service import log_action

router = APIRouter(prefix="/api/knowledge-base", tags=["knowledge_base"])


class CaseIn(BaseModel):
    defect_type: str
    category: str
    description: str
    root_cause: str
    action_taken: str
    outcome: str


class CaseOut(CaseIn):
    case_id: str
    created_by: str

    class Config:
        from_attributes = True


@router.get("", response_model=list[CaseOut])
def list_cases(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(SimilarPastCase).all()


@router.post("", response_model=CaseOut)
def create_case(
    payload: CaseIn,
    current_user: CurrentUser = Depends(require_roles(UserRole.QUALITY_MANAGER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    case_id = next_id(db, SimilarPastCase, "case_id", "CASE", 3000)
    case = SimilarPastCase(
        case_id=case_id, defect_type=payload.defect_type, category=payload.category,
        description=payload.description, root_cause=payload.root_cause, action_taken=payload.action_taken,
        outcome=payload.outcome, created_by=current_user.username, editable=True,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    log_action(db, role=current_user.role.value, action_type="kb_create", entity_type="knowledge_base_case",
               entity_id=case.case_id, user_id=current_user.id, username=current_user.username, outcome="success")
    return case


@router.put("/{case_id}", response_model=CaseOut)
def update_case(
    case_id: str,
    payload: CaseIn,
    current_user: CurrentUser = Depends(require_roles(UserRole.QUALITY_MANAGER, UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    case = db.query(SimilarPastCase).filter(SimilarPastCase.case_id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found.")
    if not case.editable:
        raise HTTPException(status_code=400, detail=f"Case {case_id} is not editable.")
    for field, value in payload.model_dump().items():
        setattr(case, field, value)
    case.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(case)
    log_action(db, role=current_user.role.value, action_type="kb_update", entity_type="knowledge_base_case",
               entity_id=case.case_id, user_id=current_user.id, username=current_user.username, outcome="success")
    return case
