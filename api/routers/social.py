from fastapi import APIRouter, Depends
from schemas.auth_dto import CurrentUser
from services.social_insights_service import (
    get_social_correlations,
    get_indicator_interpretation,
    is_known_indicator,
    get_district_crime_rates,
)
from core.exceptions import AppException
from core.security import get_current_user

router = APIRouter()


@router.get("/correlations")
def correlations(current_user: CurrentUser = Depends(get_current_user)):
    """Scatter data + Pearson r for all 4 crime-vs-socioeconomic-indicator
    charts — fast, no AI call (see social_insights_service docstring)."""
    return get_social_correlations()


@router.get("/district-crime-rates")
def district_crime_rates(current_user: CurrentUser = Depends(get_current_user)):
    """Crime rate per lakh per district — backs the Hotspot Map's
    Population-weighted choropleth view (see social_insights_service docstring)."""
    return get_district_crime_rates()


@router.get("/correlations/{indicator}/interpretation")
def interpretation(indicator: str, current_user: CurrentUser = Depends(get_current_user)):
    """One chart's AI-generated interpretation, loaded on demand from the
    frontend after the fast /correlations call already rendered the chart
    itself."""
    if not is_known_indicator(indicator):
        raise AppException(f"Unknown indicator '{indicator}'", status_code=400)
    return get_indicator_interpretation(indicator)
