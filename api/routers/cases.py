from fastapi import APIRouter, Depends, Query

from core.exceptions import AppException
from core.security import get_current_user
from schemas.auth_dto import CurrentUser
from schemas.case_dto import AccusedHistoryOut, CaseDetailOut, CaseSummaryOut, TimelineEventOut
from services.db_service import get_accused_history, get_case_full, get_station_ids_for_district, get_victims_by_case, search_accused_names, search_cases
from services.permission_service import get_district_scope
from services.timeline_service import get_case_timeline

router = APIRouter()


@router.get("/search", response_model=list[CaseSummaryOut])
def search(
    police_station_id: int | None = None,
    case_status_id: int | None = None,
    crime_major_head_id: int | None = None,
    crime_minor_head_id: int | None = None,
    crime_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    month_of_year: int | None = Query(None, ge=1, le=12),
    victim_age_band: str | None = None,
    victim_gender_id: str | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
):
    # Role-based data scoping (added 2026-07-23) — the first endpoint wired
    # up to services/permission_service.get_district_scope. None means
    # unscoped (DGP/Admin, or IGP/SP/Inspector with district_access=ALL);
    # otherwise resolves the officer's home district down to the concrete
    # police-station ids search_cases actually filters by.
    district = get_district_scope(current_user)
    station_ids = get_station_ids_for_district(district) if district else None
    try:
        return search_cases(
            police_station_id=police_station_id,
            case_status_id=case_status_id,
            crime_major_head_id=crime_major_head_id,
            crime_minor_head_id=crime_minor_head_id,
            crime_type=crime_type,
            from_date=from_date,
            to_date=to_date,
            month_of_year=month_of_year,
            victim_age_band=victim_age_band,
            victim_gender_id=victim_gender_id,
            limit=limit,
            offset=offset,
            station_ids=station_ids,
        )
    except ValueError as e:
        raise AppException(str(e), status_code=400)


@router.get("/accused/search")
def accused_search(
    q: str = Query(..., min_length=2),
    limit: int = Query(8, ge=1, le=20),
    current_user: CurrentUser = Depends(get_current_user),
):
    return search_accused_names(q, limit)


@router.get("/accused/history", response_model=list[AccusedHistoryOut])
def accused_history(name: str, current_user: CurrentUser = Depends(get_current_user)):
    if not name or not name.strip():
        raise AppException("Accused name is required", status_code=400)

    matches = get_accused_history(name)
    if not matches:
        raise AppException(f"No accused found matching '{name}'", status_code=404)

    # A partial-name search can legitimately hit several different people
    # (e.g. "Kumar" matches many unrelated accused) — group by name so their
    # case histories are never merged together as if they were one person's.
    # Grouping key is (name, age, gender), not name alone: this schema has no
    # cross-case person ID (Accused.AccusedMasterID is a per-case appearance,
    # not a stable per-human identifier — see services/db_service.py), so name
    # is already the only link tying one person's cases together. Two
    # genuinely different people who happen to share an exact name are a real,
    # unavoidable risk with no ID field to fall back on; folding in age+gender
    # (both already on the row) at least stops the most likely false merges
    # without pretending this fully solves it.
    groups: dict[tuple, list] = {}
    for m in matches:
        key = (m.get("AccusedName") or "", m.get("AgeYear"), m.get("GenderID"))
        groups.setdefault(key, []).append(m)

    results = []
    for (accused_name, _age, _gender), rows in groups.items():
        cases = [
            CaseSummaryOut(
                CaseMasterID=m["CaseMasterID"],
                CrimeNo=m.get("CrimeNo", ""),
                CrimeRegisteredDate=m.get("CrimeRegisteredDate"),
                CaseStatusID=m.get("CaseStatusID"),
                PoliceStationID=m.get("PoliceStationID"),
                BriefFacts=m.get("BriefFacts"),
            )
            for m in rows
        ]
        results.append(
            AccusedHistoryOut(
                AccusedMasterID=rows[0]["AccusedMasterID"],
                AccusedName=accused_name,
                total_cases=len(rows),
                cases=cases,
            )
        )

    return results


@router.get("/{case_master_id}/victims")
def victims_for_case(case_master_id: int, current_user: CurrentUser = Depends(get_current_user)):
    return get_victims_by_case(case_master_id)


@router.get("/{crime_no}", response_model=CaseDetailOut)
def get_case(crime_no: str, current_user: CurrentUser = Depends(get_current_user)):
    try:
        case = get_case_full(crime_no)
    except ValueError:
        raise AppException("Invalid crime number format", status_code=400)

    if not case:
        raise AppException(f"No case found for crime number '{crime_no}'", status_code=404)
    return case


@router.get("/{crime_no}/timeline", response_model=list[TimelineEventOut])
def case_timeline(crime_no: str, current_user: CurrentUser = Depends(get_current_user)):
    try:
        timeline = get_case_timeline(crime_no)
    except ValueError:
        raise AppException("Invalid crime number format", status_code=400)

    if timeline is None:
        raise AppException(f"No case found for crime number '{crime_no}'", status_code=404)
    return timeline
