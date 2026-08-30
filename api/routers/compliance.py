from fastapi import APIRouter, Depends
from schemas.auth_dto import CurrentUser
from services.compliance_service import get_chargesheet_deadlines
from services.permission_service import get_scoped_station_ids
from core.security import get_current_user

router = APIRouter()


@router.get("/chargesheet-deadlines")
def chargesheet_deadlines(current_user: CurrentUser = Depends(get_current_user)):
    """Real BNSS Section 187 chargesheet-deadline tracker — see
    services/compliance_service.get_chargesheet_deadlines. Visible to every
    role (no can_* gate — this is read-only, jurisdiction-scoped the same
    way every other case-touching read in this app already is), not
    restricted to the roles that can register/amend FIRs."""
    return get_chargesheet_deadlines(station_ids=get_scoped_station_ids(current_user))
