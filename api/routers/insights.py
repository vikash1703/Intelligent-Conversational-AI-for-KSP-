from fastapi import APIRouter, Depends
from schemas.auth_dto import CurrentUser
from services.insight_service import generate_case_summary, generate_behavioral_analysis
from services.similarity_service import find_similar_cases, get_investigative_leads
from services.mo_service import analyze_mo
from core.exceptions import AppException
from core.security import get_current_user

router = APIRouter()


@router.get("/case-summary/{crime_no}")
def case_summary(crime_no: str, current_user: CurrentUser = Depends(get_current_user)):
    # Unlike the other three endpoints below, generate_case_summary() goes through
    # db_service.get_case_full(), which enforces a crime_no format regex and raises
    # a bare ValueError on mismatch — uncaught here, that surfaced as a generic
    # unhandled 500 ("Internal Server Error", not even this app's own error JSON
    # shape) instead of the same clean 400 every other crime_no-format check in
    # this codebase produces.
    try:
        result = generate_case_summary(crime_no)
    except ValueError:
        raise AppException("Invalid crime number format", status_code=400)
    if not result:
        raise AppException(f"No case found for crime number '{crime_no}'", status_code=404)
    return result


@router.get("/behavioral-analysis")
def behavioral_analysis(name: str, current_user: CurrentUser = Depends(get_current_user)):
    result = generate_behavioral_analysis(name)
    if not result:
        raise AppException(f"No accused found matching '{name}'", status_code=404)
    return result


@router.get("/similar-cases/{crime_no}")
def similar_cases(crime_no: str, current_user: CurrentUser = Depends(get_current_user)):
    result = find_similar_cases(crime_no)
    if not result:
        raise AppException(f"No case found for crime number '{crime_no}'", status_code=404)
    return result


@router.get("/investigative-leads/{crime_no}")
def investigative_leads(crime_no: str, current_user: CurrentUser = Depends(get_current_user)):
    result = get_investigative_leads(crime_no)
    if not result:
        raise AppException(f"No case found for crime number '{crime_no}'", status_code=404)
    return result


@router.get("/mo-analysis/{crime_no}")
def mo_analysis(crime_no: str, current_user: CurrentUser = Depends(get_current_user)):
    result = analyze_mo(crime_no)
    if not result:
        raise AppException(f"No case found for crime number '{crime_no}'", status_code=404)
    return result
