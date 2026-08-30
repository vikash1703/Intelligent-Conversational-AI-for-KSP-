from fastapi import APIRouter, Depends, Query
from schemas.auth_dto import CurrentUser
from services.scoring_service import (
    get_repeat_offenders, get_offender_risk_score, get_early_warning_alerts, get_alert_top_districts,
    get_risk_score_weights, get_accused_crime_type_distribution,
)
from services.permission_service import get_scoped_station_ids
from core.exceptions import AppException
from core.security import get_current_user

router = APIRouter()


@router.get("/risk-score/weights")
def risk_score_weights(current_user: CurrentUser = Depends(get_current_user)):
    """The real formula constants — see services/scoring_service.
    get_risk_score_weights. Registered before /risk-score's own query-param
    route below isn't strictly necessary (this is a distinct literal path,
    not a path-param match), but kept adjacent for readability."""
    return get_risk_score_weights()


@router.get("/repeat-offenders")
def repeat_offenders(
    min_case_count: int = Query(2, ge=2),
    current_user: CurrentUser = Depends(get_current_user),
):
    return get_repeat_offenders(min_case_count=min_case_count, station_ids=get_scoped_station_ids(current_user))


@router.get("/accused-crime-type-distribution")
def accused_crime_type_distribution(current_user: CurrentUser = Depends(get_current_user)):
    return get_accused_crime_type_distribution(station_ids=get_scoped_station_ids(current_user))


@router.get("/risk-score")
def risk_score(name: str, current_user: CurrentUser = Depends(get_current_user)):
    result = get_offender_risk_score(name, station_ids=get_scoped_station_ids(current_user))
    if not result:
        raise AppException(f"No accused found matching '{name}'", status_code=404)
    return result


@router.get("/early-warnings")
def early_warnings(
    recent_days: int = Query(30, ge=1, le=365),
    current_user: CurrentUser = Depends(get_current_user),
):
    return get_early_warning_alerts(recent_days=recent_days, station_ids=get_scoped_station_ids(current_user))


@router.get("/early-warnings/districts")
def early_warning_districts(
    crime_type: str,
    recent_days: int = Query(30, ge=1, le=365),
    current_user: CurrentUser = Depends(get_current_user),
):
    return get_alert_top_districts(crime_type, recent_days=recent_days, station_ids=get_scoped_station_ids(current_user))
