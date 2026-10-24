from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.user import User
from backend.models.enums import UserRole
from backend.auth.dependencies import require_roles, CurrentUser
from backend.auth.security import hash_password
from backend.schemas.auth import UserOut
from pydantic import BaseModel, EmailStr, Field
from backend.services.audit_service import log_action

router = APIRouter(prefix="/api/users", tags=["users"])


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    email: EmailStr
    full_name: str
    role: str
    line_scope: str | None = None
    password: str = Field(..., min_length=6, max_length=256)


@router.get("", response_model=list[UserOut])
def list_users(current_user: CurrentUser = Depends(require_roles(UserRole.ADMIN)), db: Session = Depends(get_db)):
    return db.query(User).all()


@router.post("", response_model=UserOut)
def create_user(
    payload: CreateUserRequest,
    current_user: CurrentUser = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=400, detail=f"Username '{payload.username}' already exists.")
    try:
        role = UserRole(payload.role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown role '{payload.role}'.")
    user = User(
        username=payload.username, email=payload.email, password_hash=hash_password(payload.password),
        full_name=payload.full_name, role=role, line_scope=payload.line_scope, is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_action(db, role=current_user.role.value, action_type="create_user", entity_type="user",
               entity_id=user.username, user_id=current_user.id, username=current_user.username, outcome="success")
    return user


@router.post("/{username}/deactivate", response_model=UserOut)
def deactivate_user(username: str, current_user: CurrentUser = Depends(require_roles(UserRole.ADMIN)), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found.")
    user.is_active = False
    db.commit()
    db.refresh(user)
    log_action(db, role=current_user.role.value, action_type="deactivate_user", entity_type="user",
               entity_id=username, user_id=current_user.id, username=current_user.username, outcome="success")
    return user


@router.post("/{username}/role", response_model=UserOut)
def assign_role(username: str, role: str, current_user: CurrentUser = Depends(require_roles(UserRole.ADMIN)), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found.")
    try:
        user.role = UserRole(role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown role '{role}'.")
    db.commit()
    db.refresh(user)
    log_action(db, role=current_user.role.value, action_type="assign_role", entity_type="user",
               entity_id=username, user_id=current_user.id, username=current_user.username,
               details={"new_role": role}, outcome="success")
    return user
