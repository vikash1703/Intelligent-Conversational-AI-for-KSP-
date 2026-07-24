from fastapi import APIRouter, Depends, Query
from schemas.auth_dto import CurrentUser
from services.financial_service import get_transaction_summary, list_suspicious_transactions
from core.security import get_current_user

router = APIRouter()


@router.get("/summary")
def transaction_summary(current_user: CurrentUser = Depends(get_current_user)):
    return get_transaction_summary()


@router.get("/suspicious")
def suspicious_transactions(
    limit: int = Query(20, ge=1, le=300),
    current_user: CurrentUser = Depends(get_current_user),
):
    return list_suspicious_transactions(limit=limit)
