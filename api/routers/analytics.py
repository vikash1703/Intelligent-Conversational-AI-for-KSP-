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
from services.forecast_service import forecast_crime_trend, get_district_hotspot_forecast
from services.case_outcome_service import get_case_outcome_flow
from services.permission_service import get_scoped_station_ids
from core.exceptions import AppException
from core.security import get_current_user

router = APIRouter()


@router.get("/case-outcome-flow")
def case_outcome_flow(current_user: CurrentUser = Depends(get_current_user)):
    """Real Crime Type -> Case Status -> Chargesheet Outcome flow counts —
    backs the Analytics page's Case Outcome Flow / Sankey (Tier 1 item 7,
    added 2026-08-24). See services/case_outcome_service.get_case_outcome_flow."""
    return get_case_outcome_flow(station_ids=get_scoped_station_ids(current_user))


@router.get("/hotspots")
def hotspots(
    crime_major_head_id: int | None = None,
    crime_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = Query(3000, ge=1, le=3000),
    zoom: int | None = Query(None, ge=1, le=18, description="Grid-aggregate the response for this map zoom level instead of returning raw points"),
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        return get_crime_hotspots(
            crime_major_head_id=crime_major_head_id,
            crime_type=crime_type,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            zoom=zoom,
            station_ids=get_scoped_station_ids(current_user),
        )
    except ValueError as e:
        raise AppException(str(e), status_code=400)


@router.get("/summary")
def summary(current_user: CurrentUser = Depends(get_current_user)):
    return get_dataset_summary(station_ids=get_scoped_station_ids(current_user))


@router.get("/crime-types")
def crime_types(current_user: CurrentUser = Depends(get_current_user)):
    return get_crime_type_distribution(station_ids=get_scoped_station_ids(current_user))


@router.get("/trends")
def trends(
    from_date: str | None = None,
    to_date: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        return get_crime_trends(from_date=from_date, to_date=to_date, station_ids=get_scoped_station_ids(current_user))
    except ValueError as e:
        raise AppException(str(e), status_code=400)


@router.get("/seasonal")
def seasonal(
    from_date: str | None = None,
    to_date: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        return get_seasonal_trends(from_date=from_date, to_date=to_date, station_ids=get_scoped_station_ids(current_user))
    except ValueError as e:
        raise AppException(str(e), status_code=400)


@router.get("/demographics/victims")
def victim_demographics(current_user: CurrentUser = Depends(get_current_user)):
    # NOT scoped (2026-08-23) — Victim has no PoliceStationID of its own;
    # correct scoping needs a join through Victim.CaseMasterID against an
    # in-scope case-id list, not a plain WHERE clause. A known, reported gap
    # (see the Tier 0/1 extension report), not a silent one — every other
    # endpoint in this router is scoped, this one deliberately isn't yet.
    return get_victim_demographics()


@router.get("/forecast")
def crime_forecast(
    months_ahead: int = Query(3, ge=1, le=12),
    current_user: CurrentUser = Depends(get_current_user),
):
    return forecast_crime_trend(months_ahead=months_ahead, station_ids=get_scoped_station_ids(current_user))


@router.get("/hotspot-forecast")
def hotspot_forecast(
    crime_types: str | None = Query(None, description="Comma-separated crime types, or omit for all"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Per-district projected case count at 1/3/6 months — backs the Hotspot
    Map's Forecast layer (Tier 1 item 8, added 2026-08-24). See services/
    forecast_service.get_district_hotspot_forecast."""
    types_tuple = tuple(t.strip() for t in crime_types.split(",") if t.strip()) if crime_types else None
    return get_district_hotspot_forecast(crime_types=types_tuple, station_ids=get_scoped_station_ids(current_user))
