from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from schemas.auth_dto import CurrentUser
from services.custody_service import (
    get_custody_summary, get_upcoming_hearings, get_custody_list, get_bnss_deadline_alerts,
    get_denied_bail_crime_type_breakdown,
)
from services.pdf_service import generate_custody_hearings_report
from services.permission_service import get_scoped_station_ids, describe_scope
from core.security import get_current_user, require_permission
from core.exceptions import AppException

router = APIRouter()


@router.get("/summary")
def custody_summary(current_user: CurrentUser = Depends(get_current_user)):
    return get_custody_summary(station_ids=get_scoped_station_ids(current_user))


@router.get("/denied-breakdown")
def denied_breakdown(current_user: CurrentUser = Depends(get_current_user)):
    """Real crime-type breakdown for Denied-bail arrests — see
    services/custody_service.get_denied_bail_crime_type_breakdown."""
    return get_denied_bail_crime_type_breakdown(station_ids=get_scoped_station_ids(current_user))


@router.get("/upcoming-hearings")
def upcoming_hearings(
    within_days: int = Query(7, ge=1, le=90),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
):
    return get_upcoming_hearings(
        station_ids=get_scoped_station_ids(current_user), within_days=within_days, limit=limit, offset=offset,
    )


@router.get("/bnss-deadlines")
def bnss_deadlines(
    within_days: int = Query(7, ge=1, le=90),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
):
    return get_bnss_deadline_alerts(
        station_ids=get_scoped_station_ids(current_user), within_days=within_days, limit=limit, offset=offset,
    )


@router.get("/list")
def custody_list(
    in_custody_only: bool = False,
    police_station_id: int | None = None,
    bail_status: str | None = None,
    crime_type: str | None = None,
    name: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
):
    return get_custody_list(
        station_ids=get_scoped_station_ids(current_user), in_custody_only=in_custody_only,
        police_station_id=police_station_id, bail_status=bail_status, crime_type=crime_type,
        name=name, from_date=from_date, to_date=to_date, limit=limit, offset=offset,
    )


@router.get("/export-hearings")
def export_hearings_report(
    within_days: int = Query(7, ge=1, le=90),
    current_user: CurrentUser = Depends(require_permission("can_export")),
):
    """PDF export of the upcoming-hearings list — same real data the
    Custody Registry page's own hearings card shows. Capped at 200 rows
    (within_days=90's real max is nowhere near that on this dataset, but
    the cap keeps the PDF a reasonable size regardless)."""
    station_ids = get_scoped_station_ids(current_user)
    data = get_upcoming_hearings(station_ids=station_ids, within_days=within_days, limit=200, offset=0)
    jurisdiction = describe_scope(current_user.role.value, current_user.home_district, current_user.home_station_name)
    try:
        file_path = generate_custody_hearings_report(data["hearings"], jurisdiction)
    except Exception as e:
        raise AppException(f"Failed to generate PDF: {str(e)}", status_code=500)
    return FileResponse(path=file_path, filename="Upcoming_Hearings_Report.pdf", media_type="application/pdf")
