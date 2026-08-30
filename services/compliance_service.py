import logging
from datetime import datetime

from core.catalyst_client import execute_zcql
from core.ttl_cache import ttl_cached
from services.custody_service import _all_arrests_enriched, _scoped, _chargesheeted_case_ids

logger = logging.getLogger("ComplianceService")

# BNSS Section 187 (Bharatiya Nagarik Suraksha Sanhita, 2023 — mirrors the
# old CrPC Section 167(2) default-bail rule): 90 days for offences carrying
# death/life imprisonment/10+ years, 60 days for anything else. This app's
# 4 real crime types map onto exactly those two tracks — same mapping (and
# same underlying statutory rule) services.custody_service.
# get_bnss_deadline_alerts already established for the Notification Bell's
# BNSS section; this module is a dedicated, more detailed tracker (per-crime
# IPC section, richer status classification, progress-bar-ready fields) for
# the standalone /compliance page, not a duplicate of that feature's
# underlying rule.
_DEADLINE_DAYS_BY_CRIME_TYPE = {
    "Murder": 90,
    "Attempt to Murder": 90,
    "Theft": 60,
    "Online Fraud": 60,
}
# The single real IPC section this app's own crime-type taxonomy maps each
# type to (see services.data_quality_service's EXPECTED_IPC_SECTIONS /
# DataQualitySupervisor.jsx's own copy for the same 4-type mapping used
# elsewhere) — used here for per-card display, not a live per-case
# ActSectionAssociation lookup, since crime_type (from BriefFacts) is
# already the authoritative signal this app uses for classification
# everywhere else.
_IPC_SECTION_BY_CRIME_TYPE = {
    "Murder": "302",
    "Attempt to Murder": "307",
    "Theft": "379",
    "Online Fraud": "420",
}

# A breach crosses from "an IO can still realistically act on this in court"
# to "purely historical record" — the same 90-day window the statute itself
# uses for the longer track, reused here as a practical (not statutory)
# recency cutoff rather than inventing an unrelated number.
_RECENT_BREACH_WINDOW_DAYS = 90

COMPLIANCE_NOTE = (
    "Deadlines computed from arrest date anchored to dataset date {anchor}. "
    "BNSS S.187(3) runs from first remand date — production deployment "
    "should record remand date."
)


def _classify(days_remaining: int) -> str:
    """days_remaining >= 0 (not yet breached): CRITICAL/WARNING/ON TRACK.
    days_remaining < 0 (breached): RECENTLY BREACHED if the deadline passed
    within the last _RECENT_BREACH_WINDOW_DAYS of the anchor date, else
    HISTORICAL — see module docstring on why 90 days is the cut, and
    services.compliance_service's own investigation (2026-08-28) on why
    this split matters: with the correct arrest-based anchor, 1,179 of
    1,238 real non-chargesheeted arrests are HISTORICAL (breached years
    before the dataset's own latest arrest record) — real, but not
    something a working IO can act on today; burying the 27 genuinely
    recent breaches and 4 CRITICAL cases under 1,179 historical ones would
    make the page operationally useless despite being technically honest."""
    if days_remaining >= 0:
        if days_remaining <= 7:
            return "CRITICAL"
        if days_remaining <= 30:
            return "WARNING"
        return "ON TRACK"
    days_since_breach = -days_remaining
    return "RECENTLY BREACHED" if days_since_breach <= _RECENT_BREACH_WINDOW_DAYS else "HISTORICAL"


@ttl_cached()
def _arrest_anchor_date() -> str:
    """The real latest ArrestSurrender.ArrestSurrenderDate — deliberately
    NOT services.custody_service._anchor_date() (that one is derived from
    CaseMaster.CrimeRegisteredDate, which now includes real wall-clock-dated
    rows from the FIR Registration feature and has drifted to today's real
    date as a result). Chargesheet deadlines are computed from ARREST
    records specifically, so they need their OWN anchor drawn from the same
    table — live-verified 2026-08-28: MAX(ArrestSurrenderDate) is exactly
    2025-12-30, matching this dataset's real frozen arrest-data cutoff."""
    rows = execute_zcql("SELECT ArrestSurrender.ArrestSurrenderDate FROM ArrestSurrender ORDER BY ArrestSurrender.ArrestSurrenderDate DESC LIMIT 1")
    if not rows:
        return datetime.now().strftime("%Y-%m-%d")
    return str(rows[0].get("ArrestSurrender", rows[0])["ArrestSurrenderDate"])[:10]


@ttl_cached()
def _all_chargesheet_deadlines() -> list[dict]:
    """Real, unscoped deadline computation for every arrest with no
    chargesheet yet — cached 15 minutes (core.ttl_cache's own default,
    matching the request exactly) since this is a real per-row scan +
    classification over 1,500 arrests, not something worth recomputing on
    every page load. Jurisdiction scoping is deliberately NOT applied here
    (police_station_id is kept on each row instead) — the SAME pattern
    services.custody_service._all_arrests_enriched already uses: cache the
    expensive unscoped computation once, apply the cheap _scoped() filter
    fresh on every request, so two officers in different jurisdictions
    never share a scoped result through the cache."""
    anchor_str = _arrest_anchor_date()
    anchor = datetime.strptime(anchor_str, "%Y-%m-%d")
    chargesheeted = _chargesheeted_case_ids()
    rows = _all_arrests_enriched()

    results = []
    for r in rows:
        if not r.get("arrest_date") or r.get("case_master_id") in chargesheeted:
            continue
        crime_type = r.get("crime_type")
        deadline_days = _DEADLINE_DAYS_BY_CRIME_TYPE.get(crime_type)
        if deadline_days is None:
            # Unspecified/unrecognized crime type — no real statutory track
            # to apply, so this arrest is left out rather than guessed at.
            continue
        try:
            arrest_dt = datetime.strptime(str(r["arrest_date"])[:10], "%Y-%m-%d")
        except ValueError:
            continue

        days_elapsed = (anchor - arrest_dt).days
        days_remaining = deadline_days - days_elapsed
        # Fraction of the deadline window already used, clamped to [0, 1] —
        # what the frontend's progress bar fills to. Capped at 1.0 even when
        # breached (days_elapsed > deadline_days) so the bar itself always
        # reads as "full", with the actual overshoot carried separately in
        # days_remaining (negative) for the overflow badge instead of a
        # bar that would need to render past 100% width.
        pct_used = max(0.0, min(1.0, days_elapsed / deadline_days))
        results.append({
            "accused_name": r.get("accused_name"),
            "case_no": r.get("crime_no"),
            "crime_type": crime_type,
            "ipc_section": _IPC_SECTION_BY_CRIME_TYPE.get(crime_type),
            "arrest_date": r.get("arrest_date"),
            "deadline_days": deadline_days,
            "days_elapsed": days_elapsed,
            "days_remaining": days_remaining,
            "pct_used": round(pct_used, 4),
            "status": _classify(days_remaining),
            "police_station_id": r.get("police_station_id"),
        })
    return results


# Sort order: not-yet-breached first (soonest-due first — CRITICAL ahead of
# WARNING ahead of ON TRACK), then breaches (most-recently-breached first,
# HISTORICAL last) — an ascending sort on a single signed number can't
# express this (a plain sort on days_remaining would put the oldest 2018
# HISTORICAL breach, days_remaining ~-2900, before every real CRITICAL
# case), so status gets an explicit rank instead.
_STATUS_RANK = {"CRITICAL": 0, "WARNING": 1, "ON TRACK": 2, "RECENTLY BREACHED": 3, "HISTORICAL": 4}


def _sort_key(r: dict):
    rank = _STATUS_RANK[r["status"]]
    # Within CRITICAL/WARNING/ON TRACK: soonest-due first (ascending
    # days_remaining). Within RECENTLY BREACHED/HISTORICAL: most-recently-
    # breached first (ascending days-since-breach, i.e. descending
    # days_remaining since it's negative there).
    tiebreak = r["days_remaining"] if rank <= 2 else -r["days_remaining"]
    return (rank, tiebreak)


def get_chargesheet_deadlines(station_ids: list[int] | None = None) -> dict:
    """Real BNSS S.187 chargesheet-deadline tracker for every in-scope
    arrest with no chargesheet on record — added 2026-08-28, anchor-date
    bug fixed same day (see _arrest_anchor_date's own docstring)."""
    scoped = _scoped(_all_chargesheet_deadlines(), station_ids)
    scoped = sorted(scoped, key=_sort_key)
    alerts = [{k: v for k, v in r.items() if k != "police_station_id"} for r in scoped]

    counts = {"CRITICAL": 0, "WARNING": 0, "ON TRACK": 0, "RECENTLY BREACHED": 0, "HISTORICAL": 0}
    for a in alerts:
        counts[a["status"]] += 1

    anchor = _arrest_anchor_date()
    return {
        "alerts": alerts,
        "counts": counts,
        "anchor_date": anchor,
        "note": COMPLIANCE_NOTE.format(anchor=anchor),
    }
