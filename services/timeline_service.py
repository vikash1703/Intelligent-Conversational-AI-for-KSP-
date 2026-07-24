import logging
from datetime import datetime

from core.catalyst_client import execute_zcql
from services.db_service import get_case_full

logger = logging.getLogger("TimelineService")

_CSTYPE_LABEL = {"A": "Chargesheet filed", "B": "Closed — false case", "C": "Closed — undetected"}

# CaseStatusMaster has exactly 3 real rows (live-verified) — cached in-process
# at module scope, same convention as entity_extractor.py's crime-types/
# date-span caches: a fresh SELECT for every timeline request is pure repeated
# cost for a value that never changes for the process's lifetime. Shared
# (not private to this module) since services/network_service.py resolves the
# same CaseStatusID -> name for an accused's linked case profile.
_case_status_cache: dict[str, str] | None = None


def get_case_status_labels() -> dict[str, str]:
    global _case_status_cache
    if _case_status_cache is None:
        rows = execute_zcql("SELECT CaseStatusMaster.ROWID, CaseStatusMaster.CaseStatusName FROM CaseStatusMaster")
        _case_status_cache = {
            r.get("CaseStatusMaster", r)["ROWID"]: r.get("CaseStatusMaster", r)["CaseStatusName"] for r in rows
        }
    return _case_status_cache


def _days_between(a, b) -> int | None:
    """b - a in whole days, both date-ish values (only the first 10 chars are
    used — some of these fields carry a time component and others don't).
    Same signed convention as Cases.jsx's daysBetween(), which this mirrors:
    a positive result means b comes after a."""
    if not a or not b:
        return None
    try:
        da = datetime.strptime(str(a)[:10], "%Y-%m-%d")
        db_ = datetime.strptime(str(b)[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return (db_ - da).days


def get_case_timeline(crime_no: str) -> list[dict] | None:
    """Chronological investigation timeline assembled purely from existing
    CaseMaster/ArrestSurrender/ChargesheetDetails/CaseStatusMaster data — no
    new fields, no fabricated stages. Mirrors the client-side assembly Cases.jsx
    builds from the same case-detail payload (see buildTimeline() there), so
    the primary UI path (which already has this data loaded and doesn't need
    a second round trip) and this standalone endpoint agree on the same
    events from the same underlying facts. Returns None if the crime number
    doesn't match any case."""
    case = get_case_full(crime_no)
    if case is None:
        return None

    fir_date = case.get("CrimeRegisteredDate")
    incident_date = case.get("IncidentFromDate")
    events = []

    if incident_date:
        events.append({
            "stage": "incident",
            "date": str(incident_date)[:10],
            "label": "Incident Occurred",
            "detail": "Reported incident date for this case.",
            "flags": [],
        })

    if fir_date:
        flags = []
        detail = "FIR registered with Karnataka Police."
        delay = _days_between(incident_date, fir_date) if incident_date else None
        if delay is not None and delay > 30:
            flags.append("reporting_delay")
            detail = f"FIR registered {delay} days after the incident."
        events.append({
            "stage": "fir_registered",
            "date": str(fir_date)[:10],
            "label": "FIR Registered",
            "detail": detail,
            "flags": flags,
        })

    for arrest in case.get("arrests", []):
        date = arrest.get("ArrestSurrenderDate")
        if not date:
            continue
        accused_id = arrest.get("AccusedMasterID", "—")
        flags = []
        detail = f"Accused ID {accused_id}."
        # A positive days-between(arrest, fir) means the FIR was registered
        # AFTER the arrest — i.e. the arrest predates the FIR. Same sign
        # convention already live-verified in Cases.jsx's RECORD_SHAPE.arrests
        # warning check (an earlier `< 0` version flagged the wrong arrest).
        predates = _days_between(date, fir_date) if fir_date else None
        if predates is not None and predates > 0:
            flags.append("predates_fir")
            detail = (
                f"Accused ID {accused_id} — recorded {predates} days before the FIR was "
                "registered (possible source data issue)."
            )
        events.append({
            "stage": "arrest",
            "date": str(date)[:10],
            "label": "Arrest / Surrender",
            "detail": detail,
            "flags": flags,
        })

    for cs in case.get("chargesheets", []):
        date = cs.get("csdate")
        if not date:
            continue
        cstype_label = _CSTYPE_LABEL.get(cs.get("cstype"), cs.get("cstype") or "Chargesheet")
        flags = []
        detail = f"{cstype_label}."
        predates = _days_between(date, fir_date) if fir_date else None
        if predates is not None and predates > 0:
            flags.append("predates_fir")
            detail = f"{cstype_label} — filed {predates} days before the FIR was registered (possible source data issue)."
        events.append({
            "stage": "chargesheet",
            "date": str(date)[:10],
            "label": "Chargesheet Filed",
            "detail": detail,
            "flags": flags,
        })

    events.sort(key=lambda e: e["date"])

    status_id = case.get("CaseStatusID")
    status_name = get_case_status_labels().get(str(status_id), "Unknown") if status_id else "Unknown"
    events.append({
        "stage": "current_status",
        "date": None,
        "label": "Current Status",
        "detail": status_name,
        "flags": [],
    })

    return events
