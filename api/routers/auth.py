from fastapi import APIRouter

from core.exceptions import AppException
from core.security import create_access_token, verify_password
from schemas.auth_dto import LoginRequest, TokenResponse, UserRole
from services.user_service import get_user_by_username

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest):
    user = get_user_by_username(request.username)
    if not user or not user.get("Active", True):
        raise AppException("Invalid username or password", status_code=401)

    if not verify_password(request.password, user["PasswordHash"]):
        raise AppException("Invalid username or password", status_code=401)

    role = UserRole(user["Role"])
    token = create_access_token(
        username=user["Username"], role=role, employee_id=user.get("EmployeeID"),
        home_district=user.get("HomeDistrict"),
    )

    return TokenResponse(access_token=token, role=role, username=user["Username"])
