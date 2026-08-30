from fastapi import APIRouter, Depends, Query
from schemas.auth_dto import CurrentUser
from services.network_service import (
    get_network_for_accused,
    get_network_for_gang,
    get_accused_profile,
    analyze_gang,
    get_organized_crime_groups,
)
from core.exceptions import AppException
from core.security import require_permission

router = APIRouter()


@router.get("/organized-groups")
def organized_groups(
    limit: int | None = Query(None, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(require_permission("can_view_network")),
):
    return get_organized_crime_groups(limit=limit, offset=offset)


@router.get("/gang/{gang_name}/analysis")
def gang_analysis(
    gang_name: str,
    current_user: CurrentUser = Depends(require_permission("can_view_network")),
):
    result = analyze_gang(gang_name)
    if not result:
        raise AppException(f"No network data found for gang '{gang_name}'", status_code=404)
    return result


@router.get("/accused/{accused_id}")
def network_for_accused(
    accused_id: str,
    current_user: CurrentUser = Depends(require_permission("can_view_network")),
):
    try:
        network = get_network_for_accused(accused_id)
    except ValueError:
        raise AppException("Invalid accused_id format", status_code=400)
    if not network:
        raise AppException(f"No network data found for accused_id '{accused_id}'", status_code=404)
    return network


@router.get("/gang/{gang_name}")
def network_for_gang(
    gang_name: str,
    current_user: CurrentUser = Depends(require_permission("can_view_network")),
):
    return get_network_for_gang(gang_name)


@router.get("/profile/{accused_id}")
def accused_profile(
    accused_id: str,
    current_user: CurrentUser = Depends(require_permission("can_view_network")),
):
    try:
        return get_accused_profile(accused_id)
    except ValueError:
        raise AppException("Invalid accused_id format", status_code=400)
