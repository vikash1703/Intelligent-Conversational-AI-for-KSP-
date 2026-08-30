from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse

from core.exceptions import AppException
from core.security import get_current_user, require_permission, require_role
from schemas.auth_dto import CurrentUser
from schemas.case_dto import (
    AccusedHistoryOut, CaseDetailOut, CaseSummaryOut, TimelineEventOut,
    ChargesheetDraftOut, ChargesheetDraftPdfIn,
)
from schemas.fir_dto import (
    FIRRegistrationIn, FIRRegistrationOut, FIRAmendmentIn, FIRAmendmentOut, CrimeNoPreviewOut,
    BriefFactsDraftIn, BriefFactsDraftOut,
)
from services.db_service import (
    get_accused_history, get_case_filter_options, get_case_full, get_victims_by_case,
    search_accused_names, search_cases, search_cases_count,
)
from services.permission_service import get_scoped_station_ids
from services.timeline_service import get_case_timeline
from services.fir_service import (
    register_fir, amend_fir, generate_crime_no, resolve_registration_station, check_rate_limit, draft_brief_facts,
)
from services.chargesheet_service import can_generate, generate_draft_text
from services.pdf_service import generate_chargesheet_draft_report
from services.audit_service import log_fir_registration, log_fir_amendment, log_chargesheet_draft_generation
from datetime import datetime, timezone

router = APIRouter()


@router.get("/filter-options")
def filter_options(current_user: CurrentUser = Depends(get_current_user)):
    """Real distinct crime types / statuses / (in-scope) stations for the
    Cases page's filter dropdowns — see services/db_service.
    get_case_filter_options for why stations are scoped here but crime_types/
    statuses aren't. Registered before the /{crime_no} catch-all routes below
    so "filter-options" is never swallowed as a literal crime number."""
    return get_case_filter_options(station_ids=get_scoped_station_ids(current_user))


@router.get("/register/preview-crime-no", response_model=CrimeNoPreviewOut)
def preview_crime_no(
    station_rowid: str | None = Query(None, description="Only needed for a district-level officer (SP) with no single home station"),
    current_user: CurrentUser = Depends(require_permission("can_register_fir")),
):
    """The CrimeNo this officer's NEXT registration would generate — read-only,
    reserves/writes nothing. Registered before /{crime_no} for the same
    reason as filter-options above. See services.fir_service for the real
    District.DistrictID/Unit.UnitID-based generation and the Phase 0 finding
    on why this deliberately does not imitate the seed data's own (verified
    synthetic) CrimeNo pattern."""
    resolved_station = resolve_registration_station(current_user, station_rowid)
    preview = generate_crime_no(resolved_station)
    return {
        "next_crime_no": preview["crime_no"],
        "station_name": preview["station_name"],
        "district_name": preview["district_name"],
    }


@router.post("/register/ai-assist-brief-facts", response_model=BriefFactsDraftOut)
def ai_assist_brief_facts(
    data: BriefFactsDraftIn,
    current_user: CurrentUser = Depends(require_permission("can_register_fir")),
):
    """Drafts a BriefFacts paragraph from the fields already filled in Step 1
    — a suggestion only, never auto-submitted (see services.fir_service.
    draft_brief_facts's own docstring on why reusing chat.llm_provider here
    is a read, not an edit, of the chat pipeline)."""
    draft = draft_brief_facts(data.crime_type, data.incident_date, data.incident_time, data.incident_location)
    return {"draft": draft}


@router.post("/register", response_model=FIRRegistrationOut)
def register_case(
    data: FIRRegistrationIn,
    http_request: Request,
    current_user: CurrentUser = Depends(require_permission("can_register_fir")),
):
    """Writes a real FIR to CaseMaster (see services.fir_service.register_fir
    for the full write path, the jurisdiction-lock logic, and the crime-type/
    BriefFacts integration finding this depends on). Every attempt — success
    and failure — is audit-logged with the full submitted payload, per the
    module's explicit accountability requirement."""
    client_ip = http_request.client.host if http_request.client else "Unknown"
    check_rate_limit(current_user.username)

    try:
        result = register_fir(data, current_user, client_ip)
    except AppException as e:
        log_fir_registration(
            user_id=current_user.username, role_name=current_user.role.value, ip_address=client_ip,
            success=False, payload=data.model_dump(), error=e.message,
        )
        raise
    except Exception as e:
        log_fir_registration(
            user_id=current_user.username, role_name=current_user.role.value, ip_address=client_ip,
            success=False, payload=data.model_dump(), error=str(e),
        )
        raise AppException(f"FIR registration failed: {str(e)}", status_code=500)

    log_fir_registration(
        user_id=current_user.username, role_name=current_user.role.value, ip_address=client_ip,
        success=True, payload=data.model_dump(), crime_no=result["crime_no"],
    )
    return result


@router.patch("/{crime_no}/amend", response_model=FIRAmendmentOut)
def amend_case(
    crime_no: str,
    data: FIRAmendmentIn,
    http_request: Request,
    current_user: CurrentUser = Depends(require_permission("can_register_fir")),
):
    """Corrects an already-registered FIR (see services.fir_service.
    amend_fir for the full write path and why it's a full-resubmission
    edit, not a raw partial PATCH). Same can_register_fir gate as
    registration, plus real jurisdiction scoping — an officer can only
    amend a case within their own scope, exactly like reading one."""
    client_ip = http_request.client.host if http_request.client else "Unknown"
    check_rate_limit(current_user.username)

    try:
        result = amend_fir(crime_no, data, station_ids=get_scoped_station_ids(current_user))
    except AppException as e:
        log_fir_amendment(
            user_id=current_user.username, role_name=current_user.role.value, ip_address=client_ip,
            crime_no=crime_no, success=False, payload=data.model_dump(), error=e.message,
        )
        raise
    except Exception as e:
        log_fir_amendment(
            user_id=current_user.username, role_name=current_user.role.value, ip_address=client_ip,
            crime_no=crime_no, success=False, payload=data.model_dump(), error=str(e),
        )
        raise AppException(f"FIR amendment failed: {str(e)}", status_code=500)

    log_fir_amendment(
        user_id=current_user.username, role_name=current_user.role.value, ip_address=client_ip,
        crime_no=crime_no, success=True, payload=data.model_dump(),
    )
    return result


@router.get("/search/count")
def search_count(
    police_station_id: int | None = None,
    case_status_id: int | None = None,
    crime_major_head_id: int | None = None,
    crime_minor_head_id: int | None = None,
    crime_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    chargesheet_outcome: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Total matching rows for the SAME filters /search accepts (minus
    month_of_year/victim_age_band/victim_gender_id — see
    services/db_service.search_cases_count's docstring for why those three
    aren't supported here; chargesheet_outcome IS, added 2026-08-24 for the
    Analytics page's new Case Outcome Flow / Sankey) — backs the Cases page's
    "Showing X of Y" count, a separate lightweight call rather than changing
    /search's own response shape (which stays a bare array, unchanged, for
    every existing caller)."""
    try:
        total = search_cases_count(
            police_station_id=police_station_id,
            case_status_id=case_status_id,
            crime_major_head_id=crime_major_head_id,
            crime_minor_head_id=crime_minor_head_id,
            crime_type=crime_type,
            from_date=from_date,
            to_date=to_date,
            chargesheet_outcome=chargesheet_outcome,
            station_ids=get_scoped_station_ids(current_user),
        )
        return {"total": total}
    except ValueError as e:
        raise AppException(str(e), status_code=400)


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
    chargesheet_outcome: str | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
):
    # Role-based data scoping (added 2026-07-23, extended to a shared helper
    # 2026-08-23) — see services/permission_service.get_scoped_station_ids.
    # None means unscoped (DGP/Admin, or a role with district_access=ALL).
    station_ids = get_scoped_station_ids(current_user)
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
            chargesheet_outcome=chargesheet_outcome,
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

    station_ids = get_scoped_station_ids(current_user)
    matches = get_accused_history(name, station_ids=station_ids)
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
    return get_victims_by_case(case_master_id, station_ids=get_scoped_station_ids(current_user))


@router.get("/{crime_no}", response_model=CaseDetailOut)
def get_case(crime_no: str, current_user: CurrentUser = Depends(get_current_user)):
    try:
        case = get_case_full(crime_no, station_ids=get_scoped_station_ids(current_user))
    except ValueError:
        raise AppException("Invalid crime number format", status_code=400)

    if not case:
        raise AppException(f"No case found for crime number '{crime_no}'", status_code=404)
    return case


@router.get("/{crime_no}/timeline", response_model=list[TimelineEventOut])
def case_timeline(crime_no: str, current_user: CurrentUser = Depends(get_current_user)):
    try:
        timeline = get_case_timeline(crime_no, station_ids=get_scoped_station_ids(current_user))
    except ValueError:
        raise AppException("Invalid crime number format", status_code=400)

    if timeline is None:
        raise AppException(f"No case found for crime number '{crime_no}'", status_code=404)
    return timeline


@router.post("/{crime_no}/chargesheet-draft", response_model=ChargesheetDraftOut)
def chargesheet_draft(
    crime_no: str,
    http_request: Request,
    current_user: CurrentUser = Depends(require_role("Inspector", "SP")),
):
    """Generates an AI-assisted BNSS chargesheet draft from this case's real,
    already-recorded data (see services.chargesheet_service — the LLM is
    given only real fetched fields and is explicitly instructed never to
    invent facts, falling back to a deterministic template-filled draft if
    the LLM call itself fails). Every attempt is audit-logged, success and
    failure both, same accountability pattern as FIR registration."""
    client_ip = http_request.client.host if http_request.client else "Unknown"
    try:
        case = get_case_full(crime_no, station_ids=get_scoped_station_ids(current_user))
    except ValueError:
        raise AppException("Invalid crime number format", status_code=400)
    if not case:
        raise AppException(f"No case found for crime number '{crime_no}'", status_code=404)

    allowed, reason = can_generate(case)
    if not allowed:
        raise AppException(reason, status_code=400)

    try:
        draft_text = generate_draft_text(case)
    except Exception as e:
        log_chargesheet_draft_generation(
            user_id=current_user.username, role_name=current_user.role.value, ip_address=client_ip,
            crime_no=crime_no, success=False, error=str(e),
        )
        raise AppException(f"Chargesheet draft generation failed: {str(e)}", status_code=500)

    log_chargesheet_draft_generation(
        user_id=current_user.username, role_name=current_user.role.value, ip_address=client_ip,
        crime_no=crime_no, success=True,
    )
    return {
        "crime_no": crime_no,
        "draft_text": draft_text,
        "generated_at": datetime.now(timezone.utc),
    }


@router.post("/{crime_no}/chargesheet-draft/pdf")
def chargesheet_draft_pdf(
    crime_no: str,
    data: ChargesheetDraftPdfIn,
    current_user: CurrentUser = Depends(require_role("Inspector", "SP")),
):
    """Renders the PDF from the EXACT text the officer already previewed and
    reviewed (data.draft_text) — never a fresh LLM regeneration, so the
    downloaded document always matches what was reviewed on screen. Still
    jurisdiction-gated: the case must be in this officer's own scope even
    though the text itself arrives in the request body, so an out-of-scope
    crime_no can't be used to produce a plausible-looking official document."""
    case = get_case_full(crime_no, station_ids=get_scoped_station_ids(current_user))
    if not case:
        raise AppException(f"No case found for crime number '{crime_no}'", status_code=404)

    try:
        file_path = generate_chargesheet_draft_report(crime_no, data.draft_text)
    except Exception as e:
        raise AppException(f"Failed to generate PDF: {str(e)}", status_code=500)
    return FileResponse(path=file_path, filename=f"Chargesheet_Draft_{crime_no}.pdf", media_type="application/pdf")
