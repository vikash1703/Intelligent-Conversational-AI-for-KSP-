from fastapi import APIRouter, Depends, Query
from schemas.auth_dto import CurrentUser
from services.db_service import get_crime_hotspots
from services.analytics_service import (
    get_crime_type_distribution,
    get_crime_trends,
    get_seasonal_trends,
    get_victim_demographics,
    get_dataset_summary,
)
from services.forecast_service import forecast_crime_trend
from core.exceptions import AppException
from core.security import get_current_user

router = APIRouter()


@router.get("/hotspots")
def hotspots(
    crime_major_head_id: int | None = None,
    crime_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = Query(3000, ge=1, le=3000),
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        return get_crime_hotspots(
            crime_major_head_id=crime_major_head_id,
            crime_type=crime_type,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
        )
    except ValueError as e:
        raise AppException(str(e), status_code=400)


@router.get("/summary")
def summary(current_user: CurrentUser = Depends(get_current_user)):
    return get_dataset_summary()


@router.get("/crime-types")
def crime_types(current_user: CurrentUser = Depends(get_current_user)):
    return get_crime_type_distribution()


@router.get("/trends")
def trends(
    from_date: str | None = None,
    to_date: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        return get_crime_trends(from_date=from_date, to_date=to_date)
    except ValueError as e:
        raise AppException(str(e), status_code=400)


@router.get("/seasonal")
def seasonal(
    from_date: str | None = None,
    to_date: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        return get_seasonal_trends(from_date=from_date, to_date=to_date)
    except ValueError as e:
        raise AppException(str(e), status_code=400)


@router.get("/demographics/victims")
def victim_demographics(current_user: CurrentUser = Depends(get_current_user)):
    return get_victim_demographics()


@router.get("/forecast")
def crime_forecast(
    months_ahead: int = Query(3, ge=1, le=12),
    current_user: CurrentUser = Depends(get_current_user),
):
    return forecast_crime_trend(months_ahead=months_ahead)
