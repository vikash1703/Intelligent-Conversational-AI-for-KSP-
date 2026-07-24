import logging
import re

from core.catalyst_client import execute_zcql, zcql_escape, validate_date, CatalystQueryError
from services.analytics_service import extract_crime_type

logger = logging.getLogger("DatabaseService")

CRIME_NO_PATTERN = re.compile(r"^[A-Za-z0-9\-]{1,25}$")


def _validate_crime_no(crime_no: str) -> str:
    if not CRIME_NO_PATTERN.match(crime_no or ""):
        raise ValueError("Invalid crime number format")
    return crime_no


_ROWID_RE = re.compile(r"^\d{10,}$")


def _is_catalyst_rowid(value) -> bool:
    return value is not None and bool(_ROWID_RE.match(str(value)))


def _resolve_act_sections(rows: list[dict]) -> list[dict]:
    """ActSectionAssociation.ActCode/SectionCode mix two data-generation
    conventions on the live table (verified directly against Catalyst): about
    half of its 9,022 rows hold the real business key (ActCode="IPC",
    SectionCode="307"), the other half hold the Act/Section table's own
    Catalyst ROWID in the same columns instead — the same "FK bound to the
    referenced table's ROWID, not its business key" pattern already known
    from the 2026-07-14 backfill (see reference_ksp_db_schema memory). Both
    shapes are resolved into the same plain business-key form here so
    callers only ever see one. Every ROWID-shaped value observed live
    resolved successfully (Act=2 rows, Section=8 rows, all in-range) — the
    unresolved_id fallback below is a genuine safety net for future data
    drift, not something seen to fire on this dataset.
    """
    if not any(_is_catalyst_rowid(r.get("ActCode")) or _is_catalyst_rowid(r.get("SectionCode")) for r in rows):
        return rows

    act_by_rowid = {r["Act"]["ROWID"]: r["Act"]["ActCode"] for r in execute_zcql("SELECT ROWID, ActCode FROM Act")}
    section_by_rowid = {
        r["Section"]["ROWID"]: r["Section"]["SectionCode"] for r in execute_zcql("SELECT ROWID, SectionCode FROM Section")
    }

    resolved = []
    for r in rows:
        act_code, section_code = r.get("ActCode"), r.get("SectionCode")
        unresolved_id = None
        if _is_catalyst_rowid(act_code):
            match = act_by_rowid.get(str(act_code))
            if match is None:
                unresolved_id = act_code
            act_code = match
        if _is_catalyst_rowid(section_code):
            match = section_by_rowid.get(str(section_code))
            if match is None:
                unresolved_id = unresolved_id or section_code
            section_code = match
        resolved.append({**r, "ActCode": act_code, "SectionCode": section_code, "unresolved_id": unresolved_id})
    return resolved


def get_case_full(crime_no: str) -> dict | None:
    """Full FIR detail: CaseMaster + all linked child records, fetched as separate
    queries (rather than one large join) to avoid cross-product row duplication
    across the CaseMaster 1-to-many children."""
    safe_crime_no = zcql_escape(_validate_crime_no(crime_no))

    case_rows = execute_zcql(
        "SELECT CaseMaster.ROWID, CaseMaster.CrimeNo, CaseMaster.CaseNo, "
        "CaseMaster.CrimeRegisteredDate, CaseMaster.IncidentFromDate, CaseMaster.IncidentToDate, "
        "CaseMaster.latitude, CaseMaster.longitude, CaseMaster.BriefFacts, "
        "CaseMaster.CaseStatusID, CaseMaster.CaseCategoryID, CaseMaster.GravityOffenceID, "
        "CaseMaster.CrimeMajorHeadID, CaseMaster.CrimeMinorHeadID, CaseMaster.PoliceStationID, "
        "CaseMaster.PolicePersonID, CaseMaster.CourtID "
        "FROM CaseMaster "
        f"WHERE CaseMaster.CrimeNo = '{safe_crime_no}'"
    )
    if not case_rows:
        return None

    case = case_rows[0].get("CaseMaster", case_rows[0])
    # CaseMaster has no business-defined ID column of its own — child tables (Victim,
    # Accused, ArrestSurrender, ...) store Catalyst's own ROWID under their CaseMasterID FK.
    case_id = case.pop("ROWID")
    case["CaseMasterID"] = case_id

    def child(table: str, columns: str) -> list:
        # CaseMasterID must be passed as a quoted literal — ZCQL's bare integer literal
        # parser rejects values over 10 digits, even though the column itself is bigint.
        rows = execute_zcql(
            f"SELECT {columns} FROM {table} WHERE {table}.CaseMasterID = '{zcql_escape(str(case_id))}'"
        )
        return [r.get(table, r) for r in rows]

    case["victims"] = child(
        "Victim", "Victim.VictimMasterID, Victim.VictimName, Victim.AgeYear, Victim.GenderID, Victim.VictimPolice"
    )
    case["accused"] = child(
        "Accused", "Accused.AccusedMasterID, Accused.AccusedName, Accused.AgeYear, Accused.GenderID, Accused.PersonID"
    )
    case["complainants"] = child(
        "ComplainantDetails",
        "ComplainantDetails.ComplainantID, ComplainantDetails.ComplainantName, ComplainantDetails.AgeYear, "
        "ComplainantDetails.OccupationID, ComplainantDetails.ReligionID, ComplainantDetails.CasteID, "
        "ComplainantDetails.GenderID",
    )
    case["arrests"] = child(
        # NB: ArrestSurrenderTypeID does not exist on the live table (verified against
        # Catalyst directly) even though the ER diagram documents it — arrest-vs-surrender
        # type cannot currently be distinguished until that column is added.
        "ArrestSurrender",
        "ArrestSurrender.ArrestSurrenderID, "
        "ArrestSurrender.ArrestSurrenderDate, ArrestSurrender.AccusedMasterID, "
        "ArrestSurrender.IsAccused, ArrestSurrender.IsComplainantAccused",
    )
    case["chargesheets"] = child(
        "ChargesheetDetails", "ChargesheetDetails.CSID, ChargesheetDetails.csdate, ChargesheetDetails.cstype"
    )
    try:
        # ActSectionAssociation.CaseMasterID is a documented broken FK (live-verified
        # this session): it holds small sequential ints (361-2214 sampled) in a
        # narrower INT column, unrelated to CaseMaster.ROWID's actual
        # 43437000000xxxxxx value space — Catalyst rejects our (correctly quoted)
        # ROWID as out-of-range for that column's real type no matter how it's
        # passed, not a transient failure. Every other child() call below is left to
        # raise normally; this one specific, already-diagnosed table is the sole
        # deliberate exception, not a return to blanket error-swallowing.
        case["act_sections"] = _resolve_act_sections(child(
            # Live columns are ActCode/SectionCode (FK named after the referenced table's PK),
            # not ActID/SectionID as the ER diagram prose states.
            "ActSectionAssociation", "ActSectionAssociation.ActCode, ActSectionAssociation.SectionCode"
        ))
    except CatalystQueryError as e:
        logger.warning(f"act_sections unavailable for case {case_id} (known broken FK): {e.message}")
        case["act_sections"] = []

    return case


def get_case_details(crime_no: str):
    """Backward-compatible alias used by the chat RAG context builder."""
    return get_case_full(crime_no)


# ZCQL's hard per-query LIMIT ceiling is 300 (see project memory) — the two
# search_cases filters below (month-of-year, victim age/gender) can't be
# expressed as a ZCQL WHERE clause at all (no date-part function; a different
# table entirely), so they page through the bounded ~3000-row CaseMaster/
# ~3416-row Victim tables and filter in Python instead, same pattern already
# used by analytics_service.paginate_case_dates/get_seasonal_trends.
_SEARCH_PAGE_SIZE = 300
_SEARCH_MAX_PAGES = 10

_AGE_BAND_RANGES = {"0-18": (0, 18), "19-30": (19, 30), "31-45": (31, 45), "46-60": (46, 60), "60+": (61, 150)}

# District -> [Unit/PoliceStation ROWIDs] is small, near-static reference
# data (District has ~10-20 rows, Unit isn't much bigger) — cached in-process
# per district name, same convention as entity_extractor.py's crime-types
# cache, rather than re-resolving on every scoped request.
_district_station_cache: dict[str, list[int]] = {}


def get_station_ids_for_district(district_name: str) -> list[int]:
    """Resolves a district name (AppUser.HomeDistrict's value) to every
    police-station (Unit) ROWID within it, for scoping CaseMaster.
    PoliceStationID — see services/permission_service.get_district_scope,
    which decides WHETHER to scope a user at all; this only answers WHAT a
    district resolves to once that decision is already made.

    Returns [] (not an error) for an unrecognized district name — the
    caller's station_ids=[] then means "this filter matches nothing", the
    safe direction to fail in for an access-control feature (never silently
    fall back to unscoped/full access just because a district name didn't
    match)."""
    if district_name in _district_station_cache:
        return _district_station_cache[district_name]

    safe_name = zcql_escape(district_name)
    district_rows = execute_zcql(f"SELECT District.ROWID FROM District WHERE District.DistrictName = '{safe_name}'")
    if not district_rows:
        logger.warning(f"No District row matches HomeDistrict='{district_name}' — scoping to zero stations")
        _district_station_cache[district_name] = []
        return []
    district_id = district_rows[0].get("District", district_rows[0])["ROWID"]

    unit_rows = execute_zcql(f"SELECT Unit.ROWID FROM Unit WHERE Unit.DistrictID = '{zcql_escape(str(district_id))}'")
    station_ids = [int(r.get("Unit", r)["ROWID"]) for r in unit_rows]
    _district_station_cache[district_name] = station_ids
    return station_ids


def _case_rows_to_summaries(rows: list) -> list:
    results = []
    for r in rows:
        row = r.get("CaseMaster", r)
        row["CaseMasterID"] = row.pop("ROWID")
        results.append(row)
    return results


def _search_cases_by_month_of_year(month_of_year: int, limit: int, offset: int, station_ids: list[int] | None = None) -> list:
    """Cases whose CrimeRegisteredDate falls in this calendar month across any
    year — backs the Analytics page's seasonal-pattern chart ("January across
    all years", not a single contiguous date range). ZCQL has no date-part
    function to push this into a WHERE clause (confirmed live: LIKE itself is
    rejected on this native `date` column), so it pages through every
    CaseMaster row and matches the month in Python instead.

    station_ids (added 2026-07-23, see permission_service.get_district_scope)
    — when given, a row must ALSO have a matching PoliceStationID to be kept.
    None means unscoped (no station filter), same as every other role-scoping
    call site in this file."""
    if not 1 <= month_of_year <= 12:
        raise ValueError("month_of_year must be between 1 and 12")

    station_id_set = set(station_ids) if station_ids is not None else None
    matched = []
    for page in range(_SEARCH_MAX_PAGES):
        rows = execute_zcql(
            "SELECT CaseMaster.ROWID, CaseMaster.CrimeNo, CaseMaster.CrimeRegisteredDate, "
            "CaseMaster.CaseStatusID, CaseMaster.PoliceStationID, CaseMaster.BriefFacts "
            f"FROM CaseMaster LIMIT {page * _SEARCH_PAGE_SIZE},{_SEARCH_PAGE_SIZE}"
        )
        if not rows:
            break
        for r in rows:
            row = r.get("CaseMaster", r)
            registered = row.get("CrimeRegisteredDate")
            if not (registered and str(registered)[5:7] == f"{month_of_year:02d}"):
                continue
            if station_id_set is not None and row.get("PoliceStationID") not in station_id_set:
                continue
            matched.append(row)
        if len(rows) < _SEARCH_PAGE_SIZE:
            break

    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    return _case_rows_to_summaries([{"CaseMaster": row} for row in matched[offset:offset + limit]])


def _search_cases_by_victim(age_band: str | None, gender_id: str | None, limit: int, offset: int, station_ids: list[int] | None = None) -> list:
    """Cases linked to a Victim matching this age band or gender — backs the
    Analytics page's victim-demographics chart. Victim.CaseMasterID (now fully
    linked, see reference_ksp_db_schema memory) is looked up first, then
    CaseMaster is fetched for that page's slice of matching case ids.

    station_ids: same role-scoping contract as _search_cases_by_month_of_year
    above — added directly to the final CaseMaster WHERE clause here since,
    unlike that function, this one already ends in a real ZCQL query rather
    than an in-Python filter."""
    conditions = []
    if age_band is not None:
        if age_band not in _AGE_BAND_RANGES:
            raise ValueError(f"Invalid age_band '{age_band}'")
        lo, hi = _AGE_BAND_RANGES[age_band]
        conditions.append(f"Victim.AgeYear >= {lo} AND Victim.AgeYear <= {hi}")
    if gender_id is not None:
        conditions.append(f"Victim.GenderID = '{zcql_escape(str(gender_id))}'")
    if not conditions:
        raise ValueError("age_band or gender_id is required")

    where_clause = f" WHERE {' AND '.join(conditions)}"
    victim_rows = execute_zcql(f"SELECT Victim.CaseMasterID FROM Victim{where_clause} LIMIT {_SEARCH_PAGE_SIZE}")
    # dict.fromkeys (not a set) to keep a stable, deduplicated order — matters
    # since this list is what gets offset/limit-sliced into pages below.
    case_ids = list(dict.fromkeys(
        r.get("Victim", r)["CaseMasterID"] for r in victim_rows if r.get("Victim", r).get("CaseMasterID")
    ))
    if not case_ids:
        return []

    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    page_ids = case_ids[offset:offset + limit]
    if not page_ids:
        return []
    ids_literal = ", ".join(f"'{zcql_escape(str(cid))}'" for cid in page_ids)
    station_filter = ""
    if station_ids is not None:
        stations_literal = ", ".join(str(int(s)) for s in station_ids) or "-1"
        station_filter = f" AND CaseMaster.PoliceStationID IN ({stations_literal})"
    rows = execute_zcql(
        "SELECT CaseMaster.ROWID, CaseMaster.CrimeNo, CaseMaster.CrimeRegisteredDate, "
        "CaseMaster.CaseStatusID, CaseMaster.PoliceStationID, CaseMaster.BriefFacts "
        f"FROM CaseMaster WHERE CaseMaster.ROWID IN ({ids_literal}){station_filter}"
    )
    return _case_rows_to_summaries(rows)


def search_cases(
    police_station_id: int | None = None,
    case_status_id: int | None = None,
    crime_major_head_id: int | None = None,
    crime_minor_head_id: int | None = None,
    crime_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    month_of_year: int | None = None,
    victim_age_band: str | None = None,
    victim_gender_id: str | None = None,
    limit: int = 25,
    offset: int = 0,
    station_ids: list[int] | None = None,
) -> list:
    # station_ids: role-based data scoping (added 2026-07-23 — see
    # services/permission_service.get_district_scope and
    # api/routers/cases.py's search endpoint, the first endpoint wired up to
    # this). None means unscoped (DGP/Admin, or a role/user with nothing to
    # scope by yet) — every other role's queries below get an additional
    # PoliceStationID-membership condition on top of whatever the caller
    # explicitly asked for, never a replacement for it.
    #
    # These two filters can't be combined with the WHERE-clause filters below
    # (no ZCQL support to push either into the same query — see the two
    # helpers' docstrings) — an officer clicking one chart segment wants cases
    # matching that one dimension, not an attempt at a cross-table AND that
    # this schema/query layer can't actually express anyway.
    if month_of_year is not None:
        return _search_cases_by_month_of_year(month_of_year, limit, offset, station_ids=station_ids)
    if victim_age_band is not None or victim_gender_id is not None:
        return _search_cases_by_victim(victim_age_band, victim_gender_id, limit, offset, station_ids=station_ids)

    conditions = []
    if station_ids is not None:
        stations_literal = ", ".join(str(int(s)) for s in station_ids) or "-1"
        conditions.append(f"CaseMaster.PoliceStationID IN ({stations_literal})")
    if police_station_id is not None:
        conditions.append(f"CaseMaster.PoliceStationID = {int(police_station_id)}")
    if case_status_id is not None:
        conditions.append(f"CaseMaster.CaseStatusID = {int(case_status_id)}")
    if crime_major_head_id is not None:
        conditions.append(f"CaseMaster.CrimeMajorHeadID = {int(crime_major_head_id)}")
    if crime_minor_head_id is not None:
        conditions.append(f"CaseMaster.CrimeMinorHeadID = {int(crime_minor_head_id)}")
    if crime_type is not None:
        # CaseMaster's classification FKs are NULL everywhere (see analytics_service
        # module docstring) — crime type only exists inside BriefFacts's templated
        # "Investigation regarding {type} registered." text, so this is a substring
        # match rather than an FK equality. ZCQL's LIKE wildcard is `*`, not the
        # standard-SQL `%` (live-verified: `LIKE '%Murder%'` silently matches zero
        # rows with no error, `LIKE '*Murder*'` works correctly).
        conditions.append(f"CaseMaster.BriefFacts LIKE '*{zcql_escape(crime_type)}*'")
    if from_date is not None:
        conditions.append(f"CaseMaster.CrimeRegisteredDate >= '{zcql_escape(validate_date(from_date, 'from_date'))}'")
    if to_date is not None:
        conditions.append(f"CaseMaster.CrimeRegisteredDate <= '{zcql_escape(validate_date(to_date, 'to_date'))}'")

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))

    query = (
        "SELECT CaseMaster.ROWID, CaseMaster.CrimeNo, CaseMaster.CrimeRegisteredDate, "
        "CaseMaster.CaseStatusID, CaseMaster.PoliceStationID, CaseMaster.BriefFacts "
        "FROM CaseMaster" + where_clause +
        f" LIMIT {offset}, {limit}"
    )
    rows = execute_zcql(query)
    return _case_rows_to_summaries(rows)


_HOTSPOT_PAGE_SIZE = 300  # ZCQL's hard per-query LIMIT ceiling
_HOTSPOT_MAX_PAGES = 10   # 3000-row ceiling — matches CaseMaster's current live size


def get_crime_hotspots(
    crime_major_head_id: int | None = None,
    crime_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 3000,
) -> list:
    """GPS points for mapping — one entry per case that has a captured incident
    location, plus crime_type/CaseStatusID so the map popup can show more than
    just the crime number and date. All 3000 live CaseMaster rows currently
    have coordinates — comfortably past ZCQL's 300-row single-query cap — so
    this pages through in batches (same bounded pattern as
    analytics_service.paginate_case_dates) instead of silently truncating to
    a 10%-ish sample regardless of how narrow the filters are. A frontend
    heatmap/marker-clustering library buckets these itself, so no server-side
    aggregation is done here."""
    conditions = ["CaseMaster.latitude IS NOT NULL", "CaseMaster.longitude IS NOT NULL"]
    if crime_major_head_id is not None:
        conditions.append(f"CaseMaster.CrimeMajorHeadID = {int(crime_major_head_id)}")
    if crime_type is not None:
        # Same BriefFacts substring approach as search_cases — CrimeMajorHeadID
        # is NULL on every live row (see analytics_service module docstring).
        conditions.append(f"CaseMaster.BriefFacts LIKE '*{zcql_escape(crime_type)}*'")
    if from_date is not None:
        conditions.append(f"CaseMaster.CrimeRegisteredDate >= '{zcql_escape(validate_date(from_date, 'from_date'))}'")
    if to_date is not None:
        conditions.append(f"CaseMaster.CrimeRegisteredDate <= '{zcql_escape(validate_date(to_date, 'to_date'))}'")

    where_clause = f" WHERE {' AND '.join(conditions)}"
    limit = max(1, min(int(limit), _HOTSPOT_PAGE_SIZE * _HOTSPOT_MAX_PAGES))

    results = []
    for page in range(_HOTSPOT_MAX_PAGES):
        offset = page * _HOTSPOT_PAGE_SIZE
        if offset >= limit:
            break
        page_size = min(_HOTSPOT_PAGE_SIZE, limit - offset)
        rows = execute_zcql(
            "SELECT CaseMaster.CrimeNo, CaseMaster.latitude, CaseMaster.longitude, "
            "CaseMaster.CrimeMajorHeadID, CaseMaster.CrimeRegisteredDate, "
            "CaseMaster.BriefFacts, CaseMaster.CaseStatusID "
            "FROM CaseMaster" + where_clause +
            f" LIMIT {offset},{page_size}"
        )
        if not rows:
            break
        for r in rows:
            row = r.get("CaseMaster", r)
            # Popup-friendly crime type, not the full BriefFacts sentence —
            # same extraction analytics_service.get_crime_type_distribution uses.
            row["crime_type"] = extract_crime_type(row.pop("BriefFacts", None))
            results.append(row)
        if len(rows) < page_size:
            break
    return results


def get_accused_history(accused_name: str) -> list:
    """All AccusedMaster rows whose name contains accused_name (partial match —
    a real officer rarely has the exact full name on hand), joined with their
    CaseMaster summary, used to surface an offender's case history (repeat-offender
    / criminal-history lookups). Callers must group the returned rows by exact
    AccusedName before treating them as one person's history — a partial match
    like "Kumar" can legitimately hit several unrelated people, not just one
    person's multiple cases (see api/routers/cases.py's accused_history route)."""
    safe_name = zcql_escape(accused_name)
    rows = execute_zcql(
        "SELECT Accused.AccusedMasterID, Accused.AccusedName, Accused.CaseMasterID, "
        "Accused.AgeYear, Accused.GenderID "
        "FROM Accused "
        f"WHERE Accused.AccusedName LIKE '*{safe_name}*'"
    )
    matches = [r.get("Accused", r) for r in rows]

    for match in matches:
        case_id = match.get("CaseMasterID")
        case_rows = execute_zcql(
            "SELECT CaseMaster.ROWID, CaseMaster.CrimeNo, CaseMaster.CrimeRegisteredDate, "
            "CaseMaster.CaseStatusID, CaseMaster.PoliceStationID, CaseMaster.BriefFacts "
            f"FROM CaseMaster WHERE CaseMaster.ROWID = '{zcql_escape(str(case_id))}'"
        )
        if case_rows:
            case = case_rows[0].get("CaseMaster", case_rows[0])
            match["CrimeNo"] = case.get("CrimeNo", "")
            match["CrimeRegisteredDate"] = case.get("CrimeRegisteredDate")
            match["CaseStatusID"] = case.get("CaseStatusID")
            match["PoliceStationID"] = case.get("PoliceStationID")
            match["BriefFacts"] = case.get("BriefFacts")

    return matches


def search_accused_names(query: str, limit: int = 8) -> list[dict]:
    """Lightweight autocomplete backing the Insights page's Behavioral Analysis
    search-as-you-type input — distinct AccusedName values (with how many Accused
    rows each appears in) matching a partial name, with no CaseMaster join (that
    only matters once a name is actually picked, via get_accused_history /
    generate_behavioral_analysis). Same partial-match/name-grouping caveat as
    get_accused_history applies here too: AccusedMasterID is a per-case
    appearance, not a stable per-human id, so two different real people sharing
    an exact name are reported as a single entry."""
    safe_query = zcql_escape(query)
    rows = execute_zcql(
        f"SELECT Accused.AccusedName FROM Accused WHERE Accused.AccusedName LIKE '*{safe_query}*' LIMIT 300"
    )
    counts: dict[str, int] = {}
    for r in rows:
        name = r.get("Accused", r).get("AccusedName")
        if name:
            counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"name": name, "case_count": count} for name, count in ranked[:limit]]


def get_victims_by_case(case_master_id: int) -> list:
    rows = execute_zcql(
        "SELECT Victim.VictimMasterID, Victim.VictimName, Victim.AgeYear, Victim.GenderID, Victim.VictimPolice "
        f"FROM Victim WHERE Victim.CaseMasterID = '{zcql_escape(str(case_master_id))}'"
    )
    return [r.get("Victim", r) for r in rows]
