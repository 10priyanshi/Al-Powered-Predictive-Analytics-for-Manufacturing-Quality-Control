"""
Translates a role into the data scope it's allowed to see, per the RBAC
table in the spec. Used by every router that returns defects, alerts,
recommendations, or chatbot retrieval results - never just the frontend.
"""
from typing import Optional
from backend.auth.dependencies import CurrentUser
from backend.models.enums import UserRole

# Root-cause category each non-plant-wide role is scoped to.
ROLE_CATEGORY_SCOPE = {
    UserRole.MAINTENANCE: "equipment",
    UserRole.PRODUCTION: "process",  # production also reviews "human" category recs (see below)
    UserRole.PROCUREMENT: "material",
}

PLANT_WIDE_ROLES = (UserRole.QUALITY_MANAGER, UserRole.PLANT_HEAD, UserRole.ADMIN)


def category_scope_for_defects(user: CurrentUser) -> Optional[list[str]]:
    """Returns the list of root-cause categories this user's role may see
    on defect/RCA data, or None for unrestricted (plant-wide roles)."""
    if user.role in PLANT_WIDE_ROLES:
        return None
    if user.role == UserRole.PRODUCTION:
        return ["process", "human"]  # production supervisors also own "human" per routing rule
    if user.role in ROLE_CATEGORY_SCOPE:
        return [ROLE_CATEGORY_SCOPE[user.role]]
    if user.role == UserRole.OPERATOR:
        return None  # operator is scoped by line instead, not category - see line_scope_for_user
    return None


def assigned_role_scope_for_recommendations(user: CurrentUser) -> Optional[str]:
    """Returns the assigned_role value a Recommendation query should be
    filtered to, or None for unrestricted plant-wide roles."""
    if user.role in PLANT_WIDE_ROLES:
        return None
    return user.role.value


def line_scope_for_user(user: CurrentUser) -> Optional[str]:
    """Returns the line_code an Operator is restricted to, or None for
    every other role (who see across lines, filtered by category instead)."""
    if user.role == UserRole.OPERATOR:
        return user.line_scope
    return None


def is_plant_wide(user: CurrentUser) -> bool:
    return user.role in PLANT_WIDE_ROLES
