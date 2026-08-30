from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.config import settings
from schemas.auth_dto import CurrentUser, UserRole
from services.permission_service import get_permissions

# Uses the bcrypt package directly, not passlib's CryptContext — passlib 1.7.4
# (last released 2020, unmaintained) probes bcrypt's internals via a
# `bcrypt.__about__.__version__` attribute that modern bcrypt (4.1+, we have 5.0)
# no longer exposes, and passlib's own self-test fallback for that missing
# attribute crashes with "password cannot be longer than 72 bytes" on its very
# first hash call — live-verified this broke hash_password() for every input,
# not just long passwords, the moment a real AppUser row was first created (this
# path had never actually been exercised before, since /auth/login always short-
# circuited on the missing AppUser table itself). This is a permanent version
# incompatibility, not something a bcrypt version pin fixes for good — calling
# bcrypt directly removes the fragile passlib layer entirely.
# HTTPBearer (not OAuth2PasswordBearer) so Swagger's "Authorize" dialog shows a plain
# paste-your-token box — our /auth/login issues raw JWTs directly, there's no OAuth2
# form-post flow behind it for Swagger to drive.
bearer_scheme = HTTPBearer()


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(
    username: str, role: UserRole, employee_id: int | None = None,
    home_district: str | None = None, home_station_id: str | None = None,
    home_station_name: str | None = None, access_level: str | None = None,
    scope_label: str | None = None,
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": username,
        "role": role.value if isinstance(role, UserRole) else role,
        "employee_id": employee_id,
        # Added 2026-07-23 for role-based data scoping (see
        # services/permission_service.get_district_scope) — embedded in the
        # token itself, same as role/employee_id, so scoping a request never
        # needs its own extra DB round-trip.
        "home_district": home_district,
        # Added 2026-08-23, same reasoning — see CurrentUser.home_station_id
        # / home_station_name / access_level.
        "home_station_id": home_station_id,
        "home_station_name": home_station_name,
        "access_level": access_level,
        # See CurrentUser.scope_label — English-only precomposed fallback,
        # not what the UI header actually renders (it composes a localized
        # label from the raw fields above instead).
        "scope_label": scope_label,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> CurrentUser:
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        username = payload.get("sub")
        role = payload.get("role")
        if username is None or role is None:
            raise credentials_exception
        return CurrentUser(
            username=username, role=UserRole(role), employee_id=payload.get("employee_id"),
            home_district=payload.get("home_district"), home_station_id=payload.get("home_station_id"),
            home_station_name=payload.get("home_station_name"), access_level=payload.get("access_level"),
            scope_label=payload.get("scope_label"),
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired", headers={"WWW-Authenticate": "Bearer"})
    except (jwt.InvalidTokenError, ValueError):
        raise credentials_exception


def require_role(*allowed_roles: str):
    """Dependency factory: restrict a route to an explicit set of role
    values — e.g. Depends(require_role("Inspector", "SP")). Unlike
    require_permission (a RolePermission-table flag, for feature gates that
    should be admin-configurable per rank), this is for a feature whose
    role list is a fixed product decision, not something meant to vary by
    live RolePermission data — the Chargesheet Draft feature is explicitly
    "Inspector and SP only", not Admin/DGP/IGP, unlike can_register_fir."""
    def role_checker(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role.value not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role.value}' is not permitted to perform this action",
            )
        return current_user
    return role_checker


def require_permission(flag: str):
    """Dependency factory: restrict a route to roles whose RolePermission row (see
    services/permission_service.py) has the given boolean flag set — e.g.
    Depends(require_permission("can_view_network"))."""
    def permission_checker(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        permissions = get_permissions(current_user.role.value)
        if not permissions.get(flag):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role.value}' does not have '{flag}' permission",
            )
        return current_user
    return permission_checker
