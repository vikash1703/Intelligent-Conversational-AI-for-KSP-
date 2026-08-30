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
    # Which specific police station (Unit.ROWID, as a string — same
    # ROWID-not-business-key convention as every other FK-shaped reference in
    # this codebase) this officer is home-assigned to (AppUser.HomeStationID,
    # added 2026-08-23). Only meaningful for a Station-level role (Inspector);
    # when set, services/permission_service.get_scoped_station_ids() uses this
    # for TRUE single-station scoping instead of approximating to the whole
    # home_district. None degrades to that district-wide approximation, same
    # non-fatal pattern as home_district itself.
    home_station_id: Optional[str] = None
    # The resolved display name for home_station_id (Unit.UnitName), and this
    # role's RolePermission.district_access ("ALL"/"Zone"/"District"/
    # "Station") — both added 2026-08-23 alongside scope_label below, so the
    # UI header can compose a LOCALIZED jurisdiction label itself (the fixed
    # English phrasing in scope_label can't go through the i18n dict as-is)
    # while the actual scoping DECISION still only ever happens once,
    # server-side (services/permission_service.get_scoped_station_ids) — the
    # frontend only ever picks which language's words to wrap around these
    # real facts, never re-derives access from scratch.
    home_station_name: Optional[str] = None
    access_level: Optional[str] = None
    # English-only fallback label ("Bengaluru Urban — Koramangala PS",
    # "State — All Districts", "Jurisdiction not configured") — computed once
    # at login by services/permission_service.describe_scope, for any
    # consumer that just wants a ready string (Swagger, a non-UI API
    # client) rather than composing its own from the fields above.
    scope_label: Optional[str] = None
