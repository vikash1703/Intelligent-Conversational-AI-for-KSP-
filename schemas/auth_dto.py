from enum import Enum
from typing import Optional
from pydantic import BaseModel


class UserRole(str, Enum):
    """Matches the live Catalyst `RolePermission` table's role_name values
    (real Karnataka Police rank hierarchy), except ADMIN which is a
    system-administration bypass role, not a police rank — see
    services/permission_service.py."""
    DGP = "DGP"
    IGP = "IGP"
    SP = "SP"
    INSPECTOR = "Inspector"
    ADMIN = "Admin"


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: UserRole
    username: str


class CurrentUser(BaseModel):
    username: str
    role: UserRole
    employee_id: Optional[int] = None
    # Which district this officer is home-assigned to (AppUser.HomeDistrict,
    # added 2026-07-23 for role-based data scoping — see
    # services/permission_service.get_district_scope). None means either the
    # column doesn't exist yet in Catalyst, or this user's row hasn't been
    # assigned one — both degrade to "can't scope this user's data by
    # district", not a login failure.
    home_district: Optional[str] = None
