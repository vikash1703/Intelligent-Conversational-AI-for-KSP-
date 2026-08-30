from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from core.exceptions import AppException
from core.security import get_current_user, require_role
from schemas.auth_dto import CurrentUser
from services.permission_service import get_scoped_station_ids
from services.chargesheet_batch_service import (
    MAX_BATCH_SIZE, batch_generate, get_chargesheet_summary, get_filed_chargesheets, get_pending_chargesheets,
)

router = APIRouter()


class BatchGenerateIn(BaseModel):
    crime_nos: list[str]


@router.get("/summary")
def chargesheet_summary(current_user: CurrentUser = Depends(get_current_user)):
    """The Batch Chargesheet Manager's 4 summary cards — visible to every
    role (read-only), same as the Compliance page; only actually generating
    a draft is gated to Inspector/SP below."""
    return get_chargesheet_summary(station_ids=get_scoped_station_ids(current_user))


@router.get("/pending")
def chargesheet_pending(
    date_from: str | None = None,
    date_to: str | None = None,
    station_id: int | None = None,
    crime_type: str | None = None,
    status: str = Query("all", pattern="^(all|overdue|recent)$"),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
):
    return get_pending_chargesheets(
        station_ids=get_scoped_station_ids(current_user), date_from=date_from, date_to=date_to,
        station_id=station_id, crime_type=crime_type, status_filter=status, limit=limit, offset=offset,
    )


@router.get("/filed")
def chargesheet_filed(
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
):
    return get_filed_chargesheets(
        station_ids=get_scoped_station_ids(current_user), date_from=date_from, date_to=date_to,
        limit=limit, offset=offset,
    )


@router.post("/batch-generate")
def chargesheet_batch_generate(
    data: BatchGenerateIn,
    http_request: Request,
    current_user: CurrentUser = Depends(require_role("Inspector", "SP")),
):
    """DGP/IGP/Admin can view this whole page (the 3 GET endpoints above have
    no role gate beyond login) but only Inspector/SP can actually generate —
    the page-level "read-only for DGP/IGP" requirement is enforced here,
    server-side, not just by hiding the button client-side."""
    if not data.crime_nos:
        raise AppException("At least one crime number is required", status_code=400)
    if len(data.crime_nos) > MAX_BATCH_SIZE:
        raise AppException(f"At most {MAX_BATCH_SIZE} cases can be generated in one batch", status_code=400)

    client_ip = http_request.client.host if http_request.client else "Unknown"
    return batch_generate(
        data.crime_nos, current_user, client_ip, station_ids=get_scoped_station_ids(current_user),
    )
