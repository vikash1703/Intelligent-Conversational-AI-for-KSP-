import base64
import io
import json
import logging
import zipfile
from datetime import datetime, timedelta, timezone

from core.catalyst_client import fetch_all_rows
from core.config import settings
from core.exceptions import AppException
from core.ttl_cache import ttl_cached
from services.analytics_service import extract_crime_type
from services.chargesheet_service import can_generate, generate_draft_text
from services.compliance_service import _all_chargesheet_deadlines, _arrest_anchor_date
from services.custody_service import _scoped, _is_int_in
from services.db_service import get_case_full
from services.pdf_service import generate_chargesheet_draft_report

logger = logging.getLogger("ChargesheetBatchService")

# Gemini rate-limit protection, per the feature's own requirement — a single
# batch call fans out to one LLM composition call per case (see
# services.chargesheet_service.generate_draft_text), so this caps the worst-
# case burst, not an arbitrary UI limit.
MAX_BATCH_SIZE = 20

_CHARGESHEET_DRAFT_MARKER = "[chargesheet_draft] "


@ttl_cached()
def _registered_dates_by_crime_no() -> dict[str, str]:
    """CrimeRegisteredDate isn't carried on services.compliance_service's
    per-arrest rows (that module only needs arrest-based dates) — fetched
    once here, separately, for this page's own "Registered" column and
    date-range filter."""
    rows = fetch_all_rows("CaseMaster", ["CrimeNo", "CrimeRegisteredDate"])
    return {r["CrimeNo"]: str(r["CrimeRegisteredDate"])[:10] for r in rows if r.get("CrimeNo") and r.get("CrimeRegisteredDate")}


@ttl_cached()
def _all_pending_chargesheet_cases() -> list[dict]:
    """One row per CASE (not per accused/arrest) — built directly from
    services.compliance_service._all_chargesheet_deadlines(), the exact same
    unscoped, anchor-dated computation the Compliance page itself uses, so
    this page's BNSS badges are always identical to Compliance's for the
    same case: same anchor (2025-12-30), same 5-way status classification,
    same colors — never a second, independently-drifting computation.

    A case with several accused has several arrest rows in the source data,
    each with its own days_remaining; the one with the SMALLEST
    days_remaining (i.e. most urgent / worst status) is used to represent
    the whole case, since that's the deadline that actually governs when an
    IO needs to act — a chargesheet is filed once per case, not once per
    accused."""
    by_case: dict[str, list[dict]] = {}
    for r in _all_chargesheet_deadlines():
        by_case.setdefault(r["case_no"], []).append(r)

    registered = _registered_dates_by_crime_no()
    result = []
    for crime_no, rows in by_case.items():
        worst = min(rows, key=lambda r: r["days_remaining"])
        accused_names = sorted({r["accused_name"] for r in rows if r.get("accused_name")})
        result.append({
            "crime_no": crime_no,
            "crime_type": worst["crime_type"],
            "registered_date": registered.get(crime_no),
            "accused_count": len(rows),
            "accused_names": accused_names,
            "days_since_arrest": worst["days_elapsed"],
            "deadline_days": worst["deadline_days"],
            "days_remaining": worst["days_remaining"],
            "pct_used": worst["pct_used"],
            "status": worst["status"],
            "police_station_id": worst["police_station_id"],
        })
    return result


def _crime_nos_with_generated_draft() -> dict[str, dict]:
    """crime_no -> {user_id, timestamp} for the most recent SUCCESSFUL
    chargesheet-draft generation via this system (services.audit_service.
    log_chargesheet_draft_generation's own AuditLog rows, session_id=
    'chargesheet') — the only honest source for a "Filed By" / "generated
    via this system" fact this dataset has. ChargesheetDetails itself has no
    officer-linking column at all (live-verified via the Table Management
    API — only CSID/csdate/cstype/CaseMasterID exist), and there is no
    PolicePerson/Officer table anywhere in this schema to resolve
    CaseMaster.PolicePersonID to a name. A case whose chargesheet was filed
    through the actual legacy/manual process (i.e. never had a draft
    generated here) correctly gets no entry — shown as "—" by the caller,
    never a fabricated name."""
    # ZCQL caps LIMIT at 300 rows — fetch_all_rows's cursor pagination (see
    # its own docstring on why offset-based LIMIT silently drops/duplicates
    # rows) is the same helper custody_service/compliance_service already
    # rely on for a full-table scan; ORDER BY CREATEDTIME DESC isn't
    # available through it, so "most recent per crime_no" is resolved by
    # comparing entry_timestamp strings in Python below instead of relying
    # on row arrival order.
    rows = fetch_all_rows(
        settings.AUDIT_LOG_TABLE,
        ["user_id", "response_text", "entry_timestamp", "session_id"],
        where_clause=f" WHERE {settings.AUDIT_LOG_TABLE}.session_id = 'chargesheet'",
    )
    result: dict[str, dict] = {}
    for row in rows:
        text = row.get("response_text") or ""
        if not text.startswith(_CHARGESHEET_DRAFT_MARKER):
            continue
        try:
            payload = json.loads(text[len(_CHARGESHEET_DRAFT_MARKER):])
        except json.JSONDecodeError:
            continue
        if not payload.get("success"):
            continue
        crime_no = payload.get("crime_no")
        if not crime_no:
            continue
        timestamp = row.get("entry_timestamp") or ""
        # fetch_all_rows has no ORDER BY (cursor-paginated by ROWID, not
        # time — see its own docstring), so "most recent" is resolved here
        # by comparing entry_timestamp strings directly ("YYYY-MM-DD
        # HH:MM:SS" sorts correctly as plain text).
        existing = result.get(crime_no)
        if existing is None or timestamp > existing["timestamp"]:
            result[crime_no] = {"user_id": row.get("user_id"), "timestamp": timestamp}
    return result


@ttl_cached()
def _all_filed_chargesheets() -> list[dict]:
    """Real ChargesheetDetails rows with cstype='A' (an actually-filed
    chargesheet — same convention services.custody_service.
    _chargesheeted_case_ids already uses; 'B'/'C' are case OUTCOMES — false
    case / undetected — not a filed chargesheet), joined in Python to
    CaseMaster for crime_no/crime_type/station and to Accused for a real
    per-case count."""
    cs_rows = fetch_all_rows("ChargesheetDetails", ["CSID", "csdate", "cstype", "CaseMasterID"])
    filed = [r for r in cs_rows if r.get("cstype") == "A" and r.get("CaseMasterID")]
    if not filed:
        return []

    cases = fetch_all_rows("CaseMaster", ["CrimeNo", "BriefFacts", "PoliceStationID"])
    case_by_id = {c["ROWID"]: c for c in cases}

    accused_rows = fetch_all_rows("Accused", ["CaseMasterID"])
    accused_count_by_case: dict[str, int] = {}
    for a in accused_rows:
        cid = a.get("CaseMasterID")
        if cid:
            accused_count_by_case[cid] = accused_count_by_case.get(cid, 0) + 1

    generated_via_system = _crime_nos_with_generated_draft()

    result = []
    for r in filed:
        case = case_by_id.get(r["CaseMasterID"])
        if not case:
            continue
        crime_no = case.get("CrimeNo")
        generator = generated_via_system.get(crime_no)
        result.append({
            "crime_no": crime_no,
            "filed_date": str(r["csdate"])[:10] if r.get("csdate") else None,
            "crime_type": extract_crime_type(case.get("BriefFacts")),
            "accused_count": accused_count_by_case.get(r["CaseMasterID"], 0),
            "police_station_id": case.get("PoliceStationID"),
            "filed_by": generator["user_id"] if generator else None,
            "draft_generated_via_system": generator is not None,
        })
    return result


def get_chargesheet_summary(station_ids: list[int] | None = None) -> dict:
    """The 4 summary cards — all computed from the same cached, scoped rows
    the pending/filed table endpoints use, so the cards and the tables below
    them never disagree on a count."""
    pending = _scoped(_all_pending_chargesheet_cases(), station_ids)
    filed = _scoped(_all_filed_chargesheets(), station_ids)
    overdue = sum(1 for r in pending if r["days_remaining"] < 0)

    # Anchored to the dataset's own frozen arrest-record date (2025-12-30),
    # NOT real wall-clock today — same fix, same reasoning, as the
    # Compliance page's own anchor-date correction: real today (now well
    # into 2026) would make every 2018-2025 filing look "old", making these
    # two cards permanently read 0 regardless of actual activity.
    anchor_str = _arrest_anchor_date()
    anchor = datetime.strptime(anchor_str, "%Y-%m-%d")
    week_cutoff = (anchor - timedelta(days=7)).strftime("%Y-%m-%d")
    month_start = anchor.replace(day=1).strftime("%Y-%m-%d")
    filed_week = sum(1 for r in filed if r.get("filed_date") and week_cutoff <= r["filed_date"] <= anchor_str)
    filed_month = sum(1 for r in filed if r.get("filed_date") and month_start <= r["filed_date"] <= anchor_str)

    return {
        "pending_count": len(pending),
        "overdue_count": overdue,
        "filed_this_week": filed_week,
        "filed_this_month": filed_month,
        "anchor_date": anchor_str,
    }


def get_pending_chargesheets(
    station_ids: list[int] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    station_id: int | None = None,
    crime_type: str | None = None,
    status_filter: str = "all",
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """status_filter: "all" | "overdue" | "recent" ("Recently Registered" —
    FIR registered within the last 30 real days; unlike the BNSS day-math
    below, which stays anchored to the dataset's frozen 2025-12-30 arrest
    anchor for exact Compliance-page consistency, a FIR's own registration
    date is a genuinely real timestamp — including for FIRs filed through
    this app's own Register FIR feature — so "recently registered" uses
    real wall-clock now(), not the frozen anchor)."""
    rows = _scoped(_all_pending_chargesheet_cases(), station_ids)
    if station_id is not None:
        rows = [r for r in rows if _is_int_in(r.get("police_station_id"), {station_id})]
    if crime_type:
        rows = [r for r in rows if r.get("crime_type") == crime_type]
    if date_from:
        rows = [r for r in rows if r.get("registered_date") and r["registered_date"] >= date_from]
    if date_to:
        rows = [r for r in rows if r.get("registered_date") and r["registered_date"] <= date_to]
    if status_filter == "overdue":
        rows = [r for r in rows if r["days_remaining"] < 0]
    elif status_filter == "recent":
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        rows = [r for r in rows if r.get("registered_date") and r["registered_date"] >= cutoff]

    rows = sorted(rows, key=lambda r: r["days_remaining"])
    return {"total": len(rows), "items": rows[offset:offset + limit]}


def get_filed_chargesheets(
    station_ids: list[int] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    rows = _scoped(_all_filed_chargesheets(), station_ids)
    if date_from:
        rows = [r for r in rows if r.get("filed_date") and r["filed_date"] >= date_from]
    if date_to:
        rows = [r for r in rows if r.get("filed_date") and r["filed_date"] <= date_to]
    rows = sorted(rows, key=lambda r: r.get("filed_date") or "", reverse=True)
    return {"total": len(rows), "items": rows[offset:offset + limit]}


def batch_generate(crime_nos: list[str], officer, ip_address: str, station_ids: list[int] | None) -> dict:
    """Generates a chargesheet draft for each crime_no independently — one
    case failing (out of scope, no accused, LLM error, ...) never stops the
    rest, per the feature's explicit "others continue" requirement. Every
    attempt is audit-logged individually, same as the single-case endpoint.
    Reuses services.chargesheet_service.generate_draft_text /
    services.pdf_service.generate_chargesheet_draft_report exactly — no
    second draft-generation code path.

    Returns per-case results plus a combined zip_base64 (built here, stdlib
    zipfile, no new dependency) of every SUCCESSFUL pdf — built once,
    server-side, so the frontend's "Download all as ZIP" button doesn't need
    a client-side zip library."""
    from services.audit_service import log_chargesheet_draft_generation

    if len(crime_nos) > MAX_BATCH_SIZE:
        raise AppException(f"Batch limit is {MAX_BATCH_SIZE} cases at once", status_code=400)

    results = []
    zip_buffer = io.BytesIO()
    zf = zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED)
    any_success = False

    for crime_no in crime_nos:
        try:
            case = get_case_full(crime_no, station_ids=station_ids)
            if not case:
                raise AppException(f"No case found for crime number '{crime_no}'", status_code=404)
            allowed, reason = can_generate(case)
            if not allowed:
                raise AppException(reason, status_code=400)

            draft_text = generate_draft_text(case)
            file_path = generate_chargesheet_draft_report(crime_no, draft_text)
            with open(file_path, "rb") as f:
                pdf_bytes = f.read()

            zf.writestr(f"Chargesheet_Draft_{crime_no}.pdf", pdf_bytes)
            any_success = True

            log_chargesheet_draft_generation(
                user_id=officer.username, role_name=officer.role.value, ip_address=ip_address,
                crime_no=crime_no, success=True,
            )
            results.append({
                "crime_no": crime_no, "status": "success", "error": None,
                "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
            })
        except AppException as e:
            log_chargesheet_draft_generation(
                user_id=officer.username, role_name=officer.role.value, ip_address=ip_address,
                crime_no=crime_no, success=False, error=e.message,
            )
            results.append({"crime_no": crime_no, "status": "failed", "error": e.message, "pdf_base64": None})
        except Exception as e:
            logger.error(f"Batch chargesheet draft failed for {crime_no}: {e}")
            log_chargesheet_draft_generation(
                user_id=officer.username, role_name=officer.role.value, ip_address=ip_address,
                crime_no=crime_no, success=False, error=str(e),
            )
            results.append({"crime_no": crime_no, "status": "failed", "error": str(e), "pdf_base64": None})

    zf.close()
    zip_base64 = base64.b64encode(zip_buffer.getvalue()).decode("ascii") if any_success else None

    return {"results": results, "zip_base64": zip_base64}
