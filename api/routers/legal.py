from fastapi import APIRouter, Depends
from schemas.auth_dto import CurrentUser
from services.legal_kb_service import get_all_ipc_sections
from core.security import get_current_user

router = APIRouter()


@router.get("/ipc-sections")
def ipc_sections(current_user: CurrentUser = Depends(get_current_user)):
    """The same IPC section KB that answers chat's LEGAL_REFERENCE questions
    (data/legal_kb/ipc_sections.json) — backs the Cases page's act-section
    display so "IPC 302 — Murder" comes from one canonical source instead of
    a second, independently-maintained frontend copy."""
    return get_all_ipc_sections()
