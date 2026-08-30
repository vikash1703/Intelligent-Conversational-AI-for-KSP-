from fastapi import APIRouter, Depends, Query
from schemas.auth_dto import CurrentUser
from services.data_quality_service import get_quality_summary, get_quality_drilldown
from services.permission_service import get_scoped_station_ids
from core.security import get_current_user

router = APIRouter()


@router.get("/summary")
def quality_summary(current_user: CurrentUser = Depends(get_current_user)):
    return get_quality_summary(station_ids=get_scoped_station_ids(current_user))


@router.get("/drilldown")
def quality_drilldown(
    category: str,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
):
    return get_quality_drilldown(
        category, station_ids=get_scoped_station_ids(current_user), limit=limit, offset=offset,
    )
