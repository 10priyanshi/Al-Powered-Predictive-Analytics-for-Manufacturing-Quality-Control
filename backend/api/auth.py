from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.user import User
from backend.auth.security import verify_password
from backend.auth.jwt_handler import create_access_token
from backend.auth.dependencies import get_current_user, CurrentUser
from backend.schemas.auth import LoginRequest, TokenResponse, UserOut
from backend.services.audit_service import log_action

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        # Log failed attempts too (with no user_id if the username didn't even exist), for the audit trail.
        log_action(
            db, role="unknown", action_type="login_failed", entity_type="user",
            entity_id=payload.username, outcome="failure",
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")

    token = create_access_token(user.id, user.username, user.role.value, user.line_scope)
    log_action(
        db, role=user.role.value, action_type="login", entity_type="user", entity_id=user.username,
        user_id=user.id, username=user.username, outcome="success",
    )
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return UserOut.model_validate(user)
