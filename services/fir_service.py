import logging
import time
from collections import defaultdict
from datetime import datetime

from core.catalyst_client import execute_zcql, zcql_escape, insert_row, update_rows, delete_row, CatalystQueryError
from core.exceptions import AppException
from schemas.fir_dto import FIRRegistrationIn, FIRAmendmentIn

logger = logging.getLogger("FirService")

# Fixed category digit for every FIR this feature registers — see the Phase 0
# finding: the existing 3,000 seed rows' leading CrimeNo digit (1/2/3/4) does
# NOT correlate with any real column (CaseCategoryID, CrimeMajorHeadID,
# GravityOffenceID all checked directly, live, against it — no pattern),
# so there is no real classification scheme to derive this from. Always "1"
# for a fresh FIR is an honest simplification, not a guess at a scheme that
# doesn't actually exist in this data.
_CATEGORY_DIGIT = "1"
_MAX_GENERATION_ATTEMPTS = 3

# Simple in-process sliding-window rate limit (10 registrations/officer/min)
# — no existing rate-limiter utility anywhere in this codebase (checked),
# and this app runs as a single uvicorn process (same "in-process cache is
# fine" assumption services/auth_service.TokenManager and
# services/permission_service's own cache already make) so a per-username
# timestamp list needs no external store.
_RATE_LIMIT_MAX = 10
_RATE_LIMIT_WINDOW_SECONDS = 60
_registration_timestamps: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(username: str) -> None:
    now = time.time()
    recent = [t for t in _registration_timestamps[username] if now - t < _RATE_LIMIT_WINDOW_SECONDS]
    if len(recent) >= _RATE_LIMIT_MAX:
        raise AppException(
            f"Rate limit exceeded: max {_RATE_LIMIT_MAX} FIR registrations per minute", status_code=429,
        )
    recent.append(now)
    _registration_timestamps[username] = recent


def _resolve_station_codes(station_rowid: str) -> dict:
    """Real District.DistrictID + Unit.UnitID for a given station's Catalyst
    ROWID — added 2026-08-28 for the FIR module. These are genuinely distinct,
    real numeric-code columns from ROWID (live-verified via the Table
    Management API and cross-checked against sample data) — NOT the same as
    the synthetic pattern baked into the existing 3,000 seed CrimeNo values
    (see generate_crime_no's own docstring for that finding). Raises
    AppException(404) if the station or its district can't be resolved —
    this should only ever happen for a stale/bad station id, never silently
    produce a wrong code."""
    safe_id = zcql_escape(str(station_rowid))
    unit_rows = execute_zcql(
        f"SELECT Unit.UnitID, Unit.UnitName, Unit.DistrictID FROM Unit WHERE Unit.ROWID = '{safe_id}'"
    )
    if not unit_rows:
        raise AppException(f"Station '{station_rowid}' not found", status_code=404)
    unit = unit_rows[0].get("Unit", unit_rows[0])

    district_rowid = unit.get("DistrictID")
    district_rows = execute_zcql(
        f"SELECT District.DistrictID, District.DistrictName FROM District WHERE District.ROWID = '{zcql_escape(str(district_rowid))}'"
    )
    if not district_rows:
        raise AppException(f"District for station '{station_rowid}' not found", status_code=404)
    district = district_rows[0].get("District", district_rows[0])

    return {
        "district_code": str(district["DistrictID"]).zfill(4),
        "station_code": str(unit["UnitID"]).zfill(4),
        "district_name": district["DistrictName"],
        "station_name": unit["UnitName"],
    }


def generate_crime_no(station_rowid: str) -> dict:
    """Real, freshly-computed next CrimeNo for a station — 1-digit category +
    4-digit real District.DistrictID + 4-digit real Unit.UnitID + 4-digit
    current year + 5-digit serial (18 digits total, same structural shape as
    every existing CrimeNo).

    IMPORTANT, live-verified finding (Phase 0): the existing 3,000 seed
    CrimeNo values do NOT actually encode their own case's real district/
    station — e.g. a case whose real PoliceStationID resolves to Whitefield
    PS (District="Bengaluru Urban", DistrictID=1) carries an embedded
    district/station segment that decodes to a completely different station
    (Jayanagar PS). This function deliberately does NOT continue that
    pattern — it generates a genuinely correct, decodable CrimeNo from this
    case's REAL station, which means a fresh station's first real FIR here
    will essentially never collide with old seed data (different code
    space), only with other FIRs registered through this same feature.

    Collision handling is check-then-insert-with-retry, not a true atomic
    guarantee — Catalyst's CrimeNo column has no unique constraint we could
    add via the API (see the 401 OAUTH_SCOPE_MISMATCH finding), so a genuine
    simultaneous double-submit for the same station+year is a real, if
    narrow, residual risk. register_fir() re-checks immediately before
    insert and retries up to _MAX_GENERATION_ATTEMPTS times."""
    codes = _resolve_station_codes(station_rowid)
    year = str(datetime.now().year)
    prefix = f"{_CATEGORY_DIGIT}{codes['district_code']}{codes['station_code']}{year}"

    existing = execute_zcql(
        f"SELECT CaseMaster.CrimeNo FROM CaseMaster WHERE CaseMaster.CrimeNo LIKE '{prefix}*' "
        f"ORDER BY CaseMaster.CrimeNo DESC LIMIT 1"
    )
    next_serial = 1
    if existing:
        last_crime_no = existing[0].get("CaseMaster", existing[0])["CrimeNo"]
        try:
            next_serial = int(last_crime_no[-5:]) + 1
        except ValueError:
            next_serial = 1

    crime_no = f"{prefix}{str(next_serial).zfill(5)}"
    return {
        "crime_no": crime_no,
        "prefix": prefix,
        "next_serial": next_serial,
        "district_name": codes["district_name"],
        "station_name": codes["station_name"],
    }


def _build_brief_facts(crime_type: str, narrative: str, location: str) -> str:
    """Shared by register_fir and amend_fir. CaseMaster has no free-text
    location/address column at all (confirmed via the Table Management API
    — ROWID/CREATORID/CREATEDTIME/MODIFIEDTIME/CrimeNo/CaseNo/
    CrimeRegisteredDate/IncidentFromDate/IncidentToDate/InfoReceivedPSDate/
    latitude/longitude/BriefFacts/PolicePersonID/PoliceStationID/
    CaseCategoryID/GravityOffenceID/CrimeMajorHeadID/CrimeMinorHeadID/
    CaseStatusID/CourtID, nothing else) — a REAL bug found 2026-08-28:
    incident_location was captured, validated, and required on the form,
    then silently dropped, never written anywhere. Folded into BriefFacts
    here (same field already carrying the crime-type template + narrative)
    so it's not lost — visible on the case detail page like everything
    else stored in this column."""
    return f"Investigation regarding {crime_type} registered. {narrative.strip()} Location: {location.strip()}."


def _next_master_id(table: str, column: str) -> int:
    """Best-effort next integer id for a column with NO real uniqueness
    constraint on the live schema (Accused.AccusedMasterID — confirmed via
    Table Management API, is_unique: false). Same MAX+1 approach as
    generate_crime_no, same residual race-condition caveat — there is no
    stronger guarantee available without a schema-level unique index we
    can't add via the API."""
    rows = execute_zcql(f"SELECT {table}.{column} FROM {table} ORDER BY {table}.{column} DESC LIMIT 1")
    if not rows:
        return 1
    try:
        return int(rows[0].get(table, rows[0])[column]) + 1
    except (TypeError, ValueError):
        return 1


def resolve_registration_station(officer, requested_station_rowid: str | None = None) -> str:
    """The real station ROWID this FIR gets filed under — the security-
    sensitive core of the jurisdiction-locking requirement. Never trusts a
    client-supplied station for an officer who already has one of their own:

    - Inspector (Station-level access): AppUser.HomeStationID is always set
      for a real Inspector row in this data — used unconditionally, any
      data.station_rowid the client sent is ignored outright.
    - SP (District-level access): real AppUser rows for SP never have
      HomeStationID set (confirmed live — SP oversees a whole district, not
      one station), so the officer must pick a station via data.station_rowid
      — but it's verified server-side to actually belong to their own
      home_district before use, never trusted blindly.
    - Anyone else (no home_station_id AND no home_district, e.g. a
      misconfigured account): rejected — same fail-closed posture
      services.permission_service.get_district_scope already uses for every
      other jurisdiction-scoped write in this app."""
    if officer.home_station_id:
        return str(officer.home_station_id)

    if officer.home_district:
        if not requested_station_rowid:
            raise AppException(
                "Your role has no single home station — station_rowid is required", status_code=400,
            )
        codes = _resolve_station_codes(requested_station_rowid)
        if codes["district_name"] != officer.home_district:
            raise AppException(
                f"Station does not belong to your home district ({officer.home_district})", status_code=403,
            )
        return str(requested_station_rowid)

    raise AppException(
        "Your jurisdiction is not configured. Contact an administrator before registering an FIR.",
        status_code=403,
    )


def register_fir(data: FIRRegistrationIn, officer, ip_address: str = "internal") -> dict:
    """Writes a real FIR to CaseMaster (plus linked Accused/ComplainantDetails/
    ActSectionAssociation rows) — added 2026-08-28.

    Jurisdiction: the officer's own home station (AppUser.HomeStationID) is
    used, never a client-supplied one — a Station-level officer (Inspector)
    always has one; an SP (district-level, no single home station in this
    real data) falls back to a caller-supplied station_id that MUST be one
    of their own district's real stations, checked server-side, never
    trusted blindly (see api/routers/fir.py).

    CRITICAL integration detail, found during this build: every crime-type-
    driven view in this entire app (Cases badges, Analytics, Custody
    breakdowns, chat's own grounding, Data Quality's mismatch check) derives
    crime_type by regex-matching CaseMaster.BriefFacts against the literal
    pattern "Investigation regarding (.+?) registered." (services.
    analytics_service.extract_crime_type) — this is the ONLY place crime
    type is ever encoded on a real row; CaseCategoryID/CrimeMajorHeadID/
    CrimeMinorHeadID are not actually used by any real feature. A genuinely
    free-text, officer-authored brief_facts would return "Unspecified"
    everywhere and silently break every one of those surfaces for this new
    case. Fixed by prefixing the STORED BriefFacts with that exact sentence
    server-side (invisible to the officer, who only ever sees/edits their
    own narrative) — the officer's real text is preserved immediately after
    it, not replaced.

    No partial-write recovery: if a child insert fails after CaseMaster
    already succeeded, this is logged loudly (not silently swallowed) and
    the CaseMaster row is NOT rolled back (Catalyst's REST API has no
    transactions) — the exception re-raises so the caller gets a real 500
    and can manually reconcile via the ROWID logged, rather than the officer
    believing registration silently failed while a real, orphaned CaseMaster
    row exists."""
    station_rowid = resolve_registration_station(officer, data.station_rowid)
    codes = _resolve_station_codes(station_rowid)

    crime_no = None
    case_rowid = None
    for attempt in range(_MAX_GENERATION_ATTEMPTS):
        candidate = generate_crime_no(station_rowid)
        check = execute_zcql(
            f"SELECT CaseMaster.ROWID FROM CaseMaster WHERE CaseMaster.CrimeNo = '{zcql_escape(candidate['crime_no'])}'"
        )
        if check:
            logger.warning(f"CrimeNo collision on attempt {attempt + 1}: {candidate['crime_no']} already exists — retrying")
            continue

        stored_brief_facts = _build_brief_facts(data.crime_type, data.brief_facts, data.incident_location)
        case_payload = {
            "CrimeNo": candidate["crime_no"],
            "BriefFacts": stored_brief_facts,
            "CrimeRegisteredDate": datetime.now().strftime("%Y-%m-%d"),
            "IncidentFromDate": data.incident_date.strftime("%Y-%m-%d %H:%M:%S"),
            "PoliceStationID": station_rowid,
            "CaseStatusID": _under_investigation_status_id(),
        }
        if data.latitude is not None:
            case_payload["latitude"] = data.latitude
        if data.longitude is not None:
            case_payload["longitude"] = data.longitude

        try:
            result = insert_row("CaseMaster", case_payload)
        except CatalystQueryError as e:
            logger.error(f"CaseMaster insert failed on attempt {attempt + 1}: {e.message}")
            raise

        row = result["data"][0] if isinstance(result.get("data"), list) else result["data"]
        case_rowid = str(row["ROWID"])
        crime_no = candidate["crime_no"]
        break

    if crime_no is None:
        raise AppException(
            f"Could not generate a unique crime number after {_MAX_GENERATION_ATTEMPTS} attempts — please retry",
            status_code=409,
        )

    # Child inserts — each independently best-effort logged, but a failure
    # here re-raises (see docstring: no silent partial-write).
    try:
        if data.complainant_name:
            complainant_payload = {
                "ComplainantID": _next_master_id("ComplainantDetails", "ComplainantID"),
                "ComplainantName": data.complainant_name.strip(),
                "CaseMasterID": case_rowid,
            }
            if data.complainant_age is not None:
                complainant_payload["AgeYear"] = data.complainant_age
            if data.complainant_gender is not None:
                complainant_payload["GenderID"] = data.complainant_gender
            insert_row("ComplainantDetails", complainant_payload)
            # Also recorded as the Victim — this form has no separate victim
            # section (see schemas.fir_dto.FIRRegistrationIn's field list),
            # and the complainant genuinely IS the victim for the large
            # majority of real theft/assault-style FIRs an Inspector files.
            # Required for this case to read as a real, complete record: the
            # Data Quality Supervisor's no_victim check (services.
            # data_quality_service / the frontend's own checkCaseQuality
            # mirror) flags any case with zero linked Victim rows, and a
            # freshly-registered FIR with a real complainant but no Victim
            # row would incorrectly show up there as an entry-quality gap.
            victim_payload = {
                "VictimMasterID": _next_master_id("Victim", "VictimMasterID"),
                "VictimName": data.complainant_name.strip(),
                "VictimPolice": 0,
                "CaseMasterID": case_rowid,
            }
            if data.complainant_age is not None:
                victim_payload["AgeYear"] = data.complainant_age
            if data.complainant_gender is not None:
                victim_payload["GenderID"] = data.complainant_gender
            insert_row("Victim", victim_payload)

        if data.accused_name and data.accused_name.strip():
            accused_payload = {
                "AccusedMasterID": _next_master_id("Accused", "AccusedMasterID"),
                "AccusedName": data.accused_name.strip(),
                "CaseMasterID": case_rowid,
            }
            if data.accused_age is not None:
                accused_payload["AgeYear"] = data.accused_age
            if data.accused_gender is not None:
                accused_payload["GenderID"] = data.accused_gender
            insert_row("Accused", accused_payload)

        for section in data.ipc_sections:
            insert_row("ActSectionAssociation", {
                "ActCode": "IPC",
                "SectionCode": section,
                "CaseMasterID": case_rowid,
            })
    except CatalystQueryError as e:
        logger.error(
            f"FIR {crime_no} (CaseMaster ROWID {case_rowid}) registered but a linked-record insert "
            f"failed — case row is real and NOT rolled back (no transaction support). Needs manual "
            f"reconciliation. Error: {e.message}"
        )
        raise

    return {
        "crime_no": crime_no,
        "rowid": case_rowid,
        "registered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "station_name": codes["station_name"],
        "district_name": codes["district_name"],
        "message": f"FIR {crime_no} registered successfully.",
    }


def _first_child_rowid(table: str, case_id: str) -> str | None:
    """The real ROWID (not the business-key column, e.g. AccusedMasterID) of
    the first row in `table` linked to this case — needed to call
    update_rows, which addresses a row by its own ROWID, not by CaseMasterID.
    Returns None if no such row exists yet (amend_fir then inserts instead
    of updating)."""
    rows = execute_zcql(f"SELECT {table}.ROWID FROM {table} WHERE {table}.CaseMasterID = '{zcql_escape(case_id)}' LIMIT 1")
    if not rows:
        return None
    return str(rows[0].get(table, rows[0])["ROWID"])


def amend_fir(crime_no: str, data: FIRAmendmentIn, station_ids: list[int] | None) -> dict:
    """Corrects an already-registered FIR — added 2026-08-28, the "separate
    process" the registration success screen's own warning refers to
    ("Amendments require a separate process"). Deliberately narrower than
    a raw field-by-field PATCH: takes the SAME full field set registration
    does (a corrected resubmission, not a partial patch) and never touches
    CrimeNo, PoliceStationID, CrimeRegisteredDate, or CaseStatusID — a
    station/jurisdiction transfer and a status change (chargesheet filed,
    closed, etc.) are each their own real workflow, not an "amendment" to
    what was originally reported.

    Jurisdiction: looks the case up via services.db_service.get_case_full
    with the SAME station_ids scoping every other case-touching endpoint
    uses — an officer can only amend a case already within their own scope,
    identical enforcement to a read, not a separate rule invented for
    writes.

    IPC sections are replaced wholesale (delete every existing
    ActSectionAssociation row for this case, insert the new set) rather
    than diffed — simpler, and correct for this form's own UX (the officer
    sees and edits the full current list, not an add/remove delta).
    Accused/Victim/ComplainantDetails are UPDATED in place (by real ROWID,
    found via _first_child_rowid) when a row already exists, else inserted
    fresh — an amendment corrects the existing record's details, it
    doesn't accumulate duplicate victim/accused rows on every edit."""
    from services.db_service import get_case_full

    case = get_case_full(crime_no, station_ids=station_ids)
    if not case:
        raise AppException(f"No case found for crime number '{crime_no}'", status_code=404)
    case_id = str(case["CaseMasterID"])

    stored_brief_facts = _build_brief_facts(data.crime_type, data.brief_facts, data.incident_location)
    case_update = {"ROWID": case_id, "BriefFacts": stored_brief_facts, "IncidentFromDate": data.incident_date.strftime("%Y-%m-%d %H:%M:%S")}
    if data.latitude is not None:
        case_update["latitude"] = data.latitude
    if data.longitude is not None:
        case_update["longitude"] = data.longitude
    update_rows("CaseMaster", [case_update])

    if data.complainant_name:
        complainant_fields = {"ComplainantName": data.complainant_name.strip()}
        if data.complainant_age is not None:
            complainant_fields["AgeYear"] = data.complainant_age
        if data.complainant_gender is not None:
            complainant_fields["GenderID"] = data.complainant_gender
        existing_complainant = _first_child_rowid("ComplainantDetails", case_id)
        if existing_complainant:
            update_rows("ComplainantDetails", [{"ROWID": existing_complainant, **complainant_fields}])
        else:
            insert_row("ComplainantDetails", {"ComplainantID": _next_master_id("ComplainantDetails", "ComplainantID"), "CaseMasterID": case_id, **complainant_fields})

        victim_fields = {"VictimName": data.complainant_name.strip()}
        if data.complainant_age is not None:
            victim_fields["AgeYear"] = data.complainant_age
        if data.complainant_gender is not None:
            victim_fields["GenderID"] = data.complainant_gender
        existing_victim = _first_child_rowid("Victim", case_id)
        if existing_victim:
            update_rows("Victim", [{"ROWID": existing_victim, **victim_fields}])
        else:
            insert_row("Victim", {"VictimMasterID": _next_master_id("Victim", "VictimMasterID"), "CaseMasterID": case_id, "VictimPolice": 0, **victim_fields})

    if data.accused_name and data.accused_name.strip():
        accused_fields = {"AccusedName": data.accused_name.strip()}
        if data.accused_age is not None:
            accused_fields["AgeYear"] = data.accused_age
        if data.accused_gender is not None:
            accused_fields["GenderID"] = data.accused_gender
        existing_accused = _first_child_rowid("Accused", case_id)
        if existing_accused:
            update_rows("Accused", [{"ROWID": existing_accused, **accused_fields}])
        else:
            insert_row("Accused", {"AccusedMasterID": _next_master_id("Accused", "AccusedMasterID"), "CaseMasterID": case_id, **accused_fields})

    old_sections = execute_zcql(f"SELECT ActSectionAssociation.ROWID FROM ActSectionAssociation WHERE ActSectionAssociation.CaseMasterID = '{zcql_escape(case_id)}'")
    for r in old_sections:
        delete_row("ActSectionAssociation", r.get("ActSectionAssociation", r)["ROWID"])
    for section in data.ipc_sections:
        insert_row("ActSectionAssociation", {"ActCode": "IPC", "SectionCode": section, "CaseMasterID": case_id})

    return {
        "crime_no": crime_no,
        "amended_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": f"FIR {crime_no} updated successfully.",
    }


_status_id_cache: str | None = None


def _under_investigation_status_id() -> str:
    """Real CaseStatusMaster.ROWID for "Under Investigation" — never
    hardcoded (matches this session's established convention, e.g. Shift
    Briefing's Pending Investigations section), cached in-process since
    CaseStatusMaster is small, static reference data (3 real rows)."""
    global _status_id_cache
    if _status_id_cache is not None:
        return _status_id_cache
    from services.timeline_service import get_case_status_labels
    labels = get_case_status_labels()
    for status_id, name in labels.items():
        if name == "Under Investigation":
            _status_id_cache = status_id
            return status_id
    raise AppException("Could not resolve 'Under Investigation' case status", status_code=500)


def draft_brief_facts(crime_type: str, incident_date: str, incident_time: str, location: str) -> str:
    """"AI Assist" for the BriefFacts textarea — a genuine LLM draft, never
    auto-submitted (the officer must review/edit before registering; the
    frontend labels it "AI-drafted — verify before submission").

    Reuses chat.llm_provider.complete_with_failover — the SAME real Zia-
    primary/Groq/Gemini-fallback plumbing the chat pipeline already uses —
    rather than a second, duplicated LLM client. This is a read (import +
    call), not an edit: no file under chat/ is modified for this feature.
    task="composition" is an EXISTING registered task in chat.llm_provider.
    _TASK_CHAINS (Gemini primary, Zia fallback, Groq deliberately excluded
    — that module's own docstring: Groq isn't trusted for user-facing
    answer composition, only classification/extraction) — the correct
    existing category for a drafted, user-facing paragraph like this one.
    A made-up task name was tried first and confirmed to hard-crash
    (task_primary does a direct dict index, not .get) — "composition" is
    the right fit, not a workaround.

    Best-effort: raises AppException(502) on failure rather than silently
    returning empty text, so the frontend can show a real error instead of
    a textarea that looks drafted but is actually blank."""
    from chat.llm_provider import complete_with_failover

    prompt = (
        f"Draft a formal FIR BriefFacts entry for: Crime: {crime_type}, Date: {incident_date}, "
        f"Time: {incident_time}, Location: {location}. Write in formal Karnataka Police FIR "
        f"language, 2-3 sentences, factual only. Plain text only, no markdown formatting, no "
        f"headings, no bullet points."
    )
    try:
        text, _provider, _fallback_reason, _latency_ms = complete_with_failover(
            task="composition", prompt=prompt, temperature=0.4,
        )
        return text.strip()
    except Exception as e:
        logger.error(f"AI Assist draft failed: {e}")
        raise AppException("Could not draft brief facts right now — please write it manually", status_code=502)
