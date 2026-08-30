from fastapi import APIRouter, Depends, Query
from schemas.auth_dto import CurrentUser
from services.audit_service import get_audit_logs, AUDIT_EVENT_TYPES
from core.security import require_role

router = APIRouter()


@router.get("")
def list_audit_logs(
    limit: int = Query(100, ge=1, le=200),
    before: str | None = None,
    user_id: str | None = None,
    event_type: str | None = None,
    # Admin/DGP only — same 2-role gate AppShell's own Admin nav dropdown
    # already uses (see AppShell.jsx's isAdminRole) for who even sees a link
    # to this page; enforced here too since a nav link hiding an item is a
    # UX convenience, never the real access-control boundary.
    current_user: CurrentUser = Depends(require_role("Admin", "DGP")),
):
    return {
        "logs": get_audit_logs(limit=limit, before=before, user_id=user_id, event_type=event_type),
        "event_types": AUDIT_EVENT_TYPES,
    }
