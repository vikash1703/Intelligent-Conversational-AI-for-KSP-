from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from schemas.report_dto import ReportRequest
from schemas.auth_dto import CurrentUser
from services.pdf_service import generate_case_report, generate_conversation_report
from services.chat_history_service import get_all_messages
from services.db_service import get_case_full
from services.audit_service import get_session_language_preference
from core.security import require_permission
from core.exceptions import AppException

router = APIRouter()

@router.post("/generate")
def create_pdf_report(
    request: ReportRequest,
    current_user: CurrentUser = Depends(require_permission("can_export")),
):
    try:
        case = get_case_full(request.crime_no)
    except ValueError:
        raise AppException("Invalid crime number format", status_code=400)
    if not case:
        raise AppException(f"No case found for crime number '{request.crime_no}'", status_code=404)

    try:
        # Service function call karo
        file_path = generate_case_report(
            crime_no=request.crime_no, 
            content=request.report_content,
            author=request.author
        )
        
        # Seedha file download ke liye return karo
        return FileResponse(
            path=file_path, 
            filename=f"Case_Report_{request.crime_no}.pdf",
            media_type="application/pdf"
        )
    except Exception as e:
        raise AppException(f"Failed to generate PDF: {str(e)}", status_code=500)


@router.get("/conversation/{session_id}")
def export_conversation(
    session_id: str,
    language: str | None = None,
    current_user: CurrentUser = Depends(require_permission("can_export")),
):
    """language is an explicit override for this one export ("hi"/"kn"); when
    omitted, falls back to the session's own sticky language preference if it
    has one (see services.audit_service.get_session_language_preference) —
    so exporting a conversation that's been running in Kannada produces a
    Kannada PDF by default, without the caller needing to already know that."""
    messages = get_all_messages(session_id)
    if not messages:
        raise AppException(f"No conversation found for session '{session_id}'", status_code=404)

    target_language = language or get_session_language_preference(session_id)

    try:
        file_path = generate_conversation_report(session_id, messages, target_language)
        return FileResponse(
            path=file_path,
            filename=f"Conversation_{session_id}.pdf",
            media_type="application/pdf"
        )
    except Exception as e:
        raise AppException(f"Failed to generate PDF: {str(e)}", status_code=500)