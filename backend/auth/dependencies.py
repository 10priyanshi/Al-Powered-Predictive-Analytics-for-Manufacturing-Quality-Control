"""
FastAPI dependencies for authentication and RBAC. Every protected route
depends on `get_current_user` (or `require_roles(...)`) so role checks
happen server-side on every request, not just in the frontend UI.
"""
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from backend.auth.jwt_handler import decode_access_token
from backend.models.enums import UserRole


@dataclass
class CurrentUser:
    id: int
    username: str
    role: UserRole
    line_scope: Optional[str]


def get_current_user(authorization: Optional[str] = Header(default=None)) -> CurrentUser:
    """
    Extracts and validates the bearer token from the Authorization
    header. Raises 401 if missing/invalid/expired - there is no
    fallback to an "anonymous" role.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header. Expected 'Bearer <token>'.",
        )
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.")
    try:
        role = UserRole(payload["role"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has an invalid role claim.")
    return CurrentUser(
        id=int(payload["sub"]),
        username=payload["username"],
        role=role,
        line_scope=payload.get("line_scope"),
    )


def require_roles(*allowed_roles: UserRole):
    """
    Dependency factory: `Depends(require_roles(UserRole.ADMIN))` restricts
    a route to the given roles, returning 403 for anyone else. This is
    the server-side enforcement point - the frontend never decides access.
    """
    def _checker(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role.value}' is not permitted to perform this action.",
            )
        return current_user
    return _checker


# Convenience role groupings used throughout the routers, matching the
# RBAC table in the spec.
ROLES_REVIEW_ANY = (UserRole.QUALITY_MANAGER, UserRole.PLANT_HEAD)
ROLES_REVIEW_EQUIPMENT = (UserRole.MAINTENANCE, UserRole.QUALITY_MANAGER, UserRole.PLANT_HEAD)
ROLES_REVIEW_PROCESS = (UserRole.PRODUCTION, UserRole.QUALITY_MANAGER, UserRole.PLANT_HEAD)
ROLES_REVIEW_MATERIAL = (UserRole.PROCUREMENT, UserRole.QUALITY_MANAGER, UserRole.PLANT_HEAD)
ROLES_PLANT_WIDE_VIEW = (UserRole.QUALITY_MANAGER, UserRole.PLANT_HEAD, UserRole.ADMIN)
ROLES_ALL = tuple(UserRole)

# Category -> role routing map used by the RCA agent and enforced again
# here for recommendation approval checks.
CATEGORY_TO_ROLE = {
    "equipment": UserRole.MAINTENANCE,
    "material": UserRole.PROCUREMENT,
    "process": UserRole.PRODUCTION,
    "human": UserRole.PRODUCTION,
}


def can_review_recommendation(user: CurrentUser, assigned_role: str) -> bool:
    """Whether `user` may approve/reject/modify a recommendation routed
    to `assigned_role`. Quality manager and plant head can act on any
    recommendation regardless of category; everyone else only their own."""
    if user.role in ROLES_REVIEW_ANY:
        return True
    return user.role.value == assigned_role


def line_filter_for_user(user: CurrentUser) -> Optional[str]:
    """Returns the line_code a query must be filtered to, or None for
    plant-wide roles. Only Operator is restricted to a single line."""
    if user.role == UserRole.OPERATOR:
        return user.line_scope
    return None
