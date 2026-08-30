from fastapi import APIRouter

from core.exceptions import AppException
from core.security import create_access_token, verify_password
from schemas.auth_dto import LoginRequest, TokenResponse, UserRole
from services.user_service import get_user_by_username
from services.db_service import resolve_station_name
from services.permission_service import describe_scope, get_permissions

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest):
    user = get_user_by_username(request.username)
    if not user or not user.get("Active", True):
        raise AppException("Invalid username or password", status_code=401)

    if not verify_password(request.password, user["PasswordHash"]):
        raise AppException("Invalid username or password", status_code=401)

    role = UserRole(user["Role"])
    home_district = user.get("HomeDistrict")
    home_station_id = user.get("HomeStationID")
    # Resolved once, here, not on every request — see
    # services/db_service.resolve_station_name and
    # services/permission_service.describe_scope.
    home_station_name = resolve_station_name(home_station_id) if home_station_id else None
    access_level = get_permissions(role.value).get("district_access")
    scope_label = describe_scope(role.value, home_district, home_station_name)
    token = create_access_token(
        username=user["Username"], role=role, employee_id=user.get("EmployeeID"),
        home_district=home_district, home_station_id=home_station_id,
        home_station_name=home_station_name, access_level=access_level, scope_label=scope_label,
    )
    return TokenResponse(access_token=token, role=role, username=user["Username"])
