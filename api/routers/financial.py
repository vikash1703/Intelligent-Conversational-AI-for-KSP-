from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from schemas.auth_dto import CurrentUser
from services.financial_service import (
    get_transaction_summary, list_suspicious_transactions, get_suspicious_type_context,
    get_monthly_transaction_summary,
)
from services.pdf_service import generate_financial_report
from core.security import get_current_user, require_permission
from core.exceptions import AppException

router = APIRouter()


@router.get("/summary")
def transaction_summary(current_user: CurrentUser = Depends(get_current_user)):
    return get_transaction_summary()


@router.get("/suspicious")
def suspicious_transactions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    transaction_type: str | None = Query(None),
    amount_min: float | None = Query(None, ge=0),
    amount_max: float | None = Query(None, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
):
    # NOT jurisdiction-scoped — see services/financial_service module
    # docstring: FinancialTransaction has no real case/station/district link
    # of any kind, confirmed 2026-08-23. Deliberate, not an oversight.
    return list_suspicious_transactions(
        limit=limit, offset=offset, transaction_type=transaction_type,
        amount_min=amount_min, amount_max=amount_max,
    )


@router.get("/suspicious/type-context")
def suspicious_type_context(current_user: CurrentUser = Depends(get_current_user)):
    """Real, live-computed per-type suspicious rate and high-tail amount —
    see services/financial_service.get_suspicious_type_context. Powers the
    UI's honest "statistical context" explanation, shown separately from
    per-row context so it only needs fetching once, not once per row."""
    return get_suspicious_type_context()


@router.get("/monthly-summary")
def monthly_summary(current_user: CurrentUser = Depends(get_current_user)):
    """Real transaction volume by month x suspicious flag — see
    services/financial_service.get_monthly_transaction_summary."""
    return get_monthly_transaction_summary()


@router.get("/export")
def export_suspicious_report(
    transaction_type: str | None = Query(None),
    amount_min: float | None = Query(None, ge=0),
    amount_max: float | None = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=500),
    # can_export, not just a valid login — same gate /report/generate already
    # requires for the case-report PDF, applied consistently here.
    current_user: CurrentUser = Depends(require_permission("can_export")),
):
    """PDF export of the suspicious-transactions list, same filter/ordering
    the UI itself shows (real amount-descending, optional type + amount-
    range filter) — formats data list_suspicious_transactions already
    computes, no new aggregation. Capped at 500 rows so the PDF stays a
    reasonable size."""
    data = list_suspicious_transactions(
        limit=limit, offset=0, transaction_type=transaction_type, amount_min=amount_min, amount_max=amount_max,
    )
    summary = get_transaction_summary()
    suspicious_flag = next((s for s in summary["by_suspicious_flag"] if s["is_suspicious"]), None)
    try:
        file_path = generate_financial_report(
            data["transactions"],
            {"total": data["total"], "flagged": suspicious_flag["count"] if suspicious_flag else "-"},
        )
    except Exception as e:
        raise AppException(f"Failed to generate PDF: {str(e)}", status_code=500)
    return FileResponse(path=file_path, filename="Suspicious_Transactions_Report.pdf", media_type="application/pdf")
