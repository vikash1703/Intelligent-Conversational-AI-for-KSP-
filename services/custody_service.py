import logging
import random
from datetime import datetime, timedelta

from core.catalyst_client import fetch_all_rows
from core.ttl_cache import ttl_cached
from services.analytics_service import extract_crime_type, paginate_case_dates

logger = logging.getLogger("CustodyService")

# Tier 1 item 9 (Custody Registry), added 2026-08-24. release_date/
# bail_status/bail_amount/custody_type are REAL Catalyst columns, populated
# by scripts/populate_custody_data.py (see that script's own docstring for
# the full data-provenance note: 4 of 5 columns the user believed they'd
# added actually exist — next_hearing_date does not, live-verified via the
# Table Management API). next_hearing_date is therefore never read from the
# database here — it's computed below, deterministically, only for
# "Pending" records, and is NOT persisted.


@ttl_cached()
def _anchor_date() -> str:
    """The dataset's own latest CrimeRegisteredDate — same "today" concept
    chat/entity_extractor.get_dataset_anchor_date uses (not duplicated via
    cross-import: that lives in the chat layer, which imports FROM services,
    never the other way — same small-helper-duplication convention already
    used for _nearest_district/_distance_km across several service files)."""
    rows = paginate_case_dates(None, None)
    dates = [str(r["CrimeRegisteredDate"])[:10] for r in rows if r.get("CrimeRegisteredDate")]
    return max(dates) if dates else datetime.now().strftime("%Y-%m-%d")


def simulated_next_hearing_date(arrest_surrender_id: str, bail_status: str) -> str | None:
    """Deterministic, seeded by arrest_surrender_id (not persisted — see
    module docstring) — only "Pending" custody records have an upcoming
    hearing at all; Granted (already resolved) and Denied (no hearing
    pending) both return None. Biased so a real, visible fraction of Pending
    records fall within the next 7 days of the dataset's own anchor date
    (~20%), the rest spread out over the following ~60 days — otherwise the
    "Upcoming hearings within 7 days" view would be empty or coincidentally
    all-or-nothing on every load."""
    if bail_status != "Pending":
        return None
    rng = random.Random(f"hearing:{arrest_surrender_id}")
    anchor = datetime.strptime(_anchor_date(), "%Y-%m-%d")
    days_ahead = rng.randint(1, 7) if rng.random() < 0.2 else rng.randint(8, 60)
    return (anchor + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def _is_int_in(value, id_set: set[int]) -> bool:
    try:
        return int(value) in id_set
    except (TypeError, ValueError):
        return False


@ttl_cached()
def _all_arrests_enriched() -> list[dict]:
    """Every real ArrestSurrender row, joined (in Python — no ZCQL join
    support in this project) to its case's crime_type and PoliceStationID,
    and its accused's real name — one full scan, cached, shared by every
    custody-registry view below rather than each re-scanning independently."""
    arrests = fetch_all_rows(
        "ArrestSurrender",
        ["ArrestSurrenderDate", "CaseMasterID", "AccusedMasterID", "release_date", "bail_status", "bail_amount", "custody_type"],
    )
    # CrimeNo added 2026-08-26 — the registry's list/hearings views need it to
    # link an arrest row to its real case (frontend nav to /cases opens by
    # crime number, not the internal CaseMaster ROWID this table stores).
    cases = fetch_all_rows("CaseMaster", ["CrimeNo", "BriefFacts", "PoliceStationID"])
    case_by_id = {c["ROWID"]: c for c in cases}

    accused_ids = {a["AccusedMasterID"] for a in arrests if a.get("AccusedMasterID")}
    accused_names = {}
    if accused_ids:
        from core.catalyst_client import execute_zcql, zcql_escape
        ids_literal = ", ".join(f"'{zcql_escape(str(i))}'" for i in accused_ids)
        # Batched — same _JOIN_BATCH_SIZE-style concern as chat/zcql_builder.py
        # for a large IN-list (1,500 arrests is comfortably batchable at 100).
        id_list = list(accused_ids)
        for i in range(0, len(id_list), 100):
            batch = id_list[i:i + 100]
            batch_literal = ", ".join(f"'{zcql_escape(str(i))}'" for i in batch)
            rows = execute_zcql(f"SELECT Accused.ROWID, Accused.AccusedName FROM Accused WHERE Accused.ROWID IN ({batch_literal})")
            for r in rows:
                row = r.get("Accused", r)
                accused_names[row["ROWID"]] = row.get("AccusedName")

    enriched = []
    for a in arrests:
        case = case_by_id.get(a.get("CaseMasterID"), {})
        row = {
            "arrest_surrender_id": a["ROWID"],
            "arrest_date": a.get("ArrestSurrenderDate"),
            "case_master_id": a.get("CaseMasterID"),
            "crime_no": case.get("CrimeNo"),
            "accused_master_id": a.get("AccusedMasterID"),
            "accused_name": accused_names.get(a.get("AccusedMasterID")),
            "crime_type": extract_crime_type(case.get("BriefFacts")),
            "police_station_id": case.get("PoliceStationID"),
            "release_date": a.get("release_date"),
            "bail_status": a.get("bail_status"),
            "bail_amount": a.get("bail_amount"),
            "custody_type": a.get("custody_type"),
        }
        row["next_hearing_date"] = simulated_next_hearing_date(row["arrest_surrender_id"], row["bail_status"])
        row["in_custody"] = row["release_date"] is None
        enriched.append(row)
    return enriched


def _scoped(rows: list[dict], station_ids: list[int] | None) -> list[dict]:
    if station_ids is None:
        return rows
    station_id_set = set(station_ids)
    return [r for r in rows if _is_int_in(r.get("police_station_id"), station_id_set)]


def get_custody_summary(station_ids: list[int] | None = None) -> dict:
    """Aggregate counts for the Custody Registry's summary cards — real
    scan, scoped like every other case-touching aggregate in this app."""
    rows = _scoped(_all_arrests_enriched(), station_ids)
    total = len(rows)
    in_custody = sum(1 for r in rows if r["in_custody"])
    released = total - in_custody

    bail_counts: dict[str, int] = {}
    for r in rows:
        status = r["bail_status"] or "Unknown"
        bail_counts[status] = bail_counts.get(status, 0) + 1

    # Average custody duration, only computable for real released records
    # (release_date - arrest_date is a real elapsed duration there; a still-
    # in-custody record has no end date to measure from).
    durations_by_type: dict[str, list[int]] = {}
    for r in rows:
        if not (r["release_date"] and r["arrest_date"]):
            continue
        try:
            d_arrest = datetime.strptime(str(r["arrest_date"])[:10], "%Y-%m-%d")
            d_release = datetime.strptime(str(r["release_date"])[:10], "%Y-%m-%d")
        except ValueError:
            continue
        delta = (d_release - d_arrest).days
        if delta < 0:
            continue
        durations_by_type.setdefault(r["crime_type"] or "Unknown", []).append(delta)

    avg_duration_by_type = [
        {"crime_type": ct, "avg_days": round(sum(vals) / len(vals), 1), "count": len(vals)}
        for ct, vals in sorted(durations_by_type.items())
    ]

    upcoming_7d = sum(
        1 for r in rows
        if r["next_hearing_date"] and r["next_hearing_date"] <= (datetime.strptime(_anchor_date(), "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
    )

    return {
        "total_arrests": total,
        "in_custody": in_custody,
        "released": released,
        "bail_status_breakdown": [{"status": k, "count": v} for k, v in sorted(bail_counts.items())],
        "avg_custody_duration_by_crime_type": avg_duration_by_type,
        "upcoming_hearings_7d_count": upcoming_7d,
        "anchor_date": _anchor_date(),
    }


def get_upcoming_hearings(station_ids: list[int] | None = None, within_days: int = 7, limit: int = 25, offset: int = 0) -> dict:
    """Real, paginated list of Pending-bail records with a (simulated,
    disclosed) next_hearing_date within `within_days` of the dataset's own
    anchor date — sorted soonest first, the operationally useful ordering
    for "what do I need to prepare for this week"."""
    rows = _scoped(_all_arrests_enriched(), station_ids)
    cutoff = (datetime.strptime(_anchor_date(), "%Y-%m-%d") + timedelta(days=within_days)).strftime("%Y-%m-%d")
    matching = [r for r in rows if r["next_hearing_date"] and r["next_hearing_date"] <= cutoff]
    matching.sort(key=lambda r: r["next_hearing_date"])
    return {"total": len(matching), "hearings": matching[offset:offset + limit]}


# Section 187 BNSS (Bharatiya Nagarik Suraksha Sanhita, in force since
# 2024-07-01, mirrors the old CrPC Section 167(2) default-bail rule): if the
# chargesheet isn't filed within 90 days of arrest for an offence carrying
# death/life imprisonment/10+ years, or 60 days for any other offence, the
# accused becomes entitled to default bail. Real, checkable statutory rule,
# not an invented deadline — mapped onto this dataset's real crime types the
# same way _SEVERITY_POINTS in scoring_service.py already classifies them
# (Murder/Attempt to Murder carry a life-imprisonment-range sentence under
# IPC 302/307; Theft/Online Fraud do not).
_BNSS_90_DAY_CRIME_TYPES = {"Murder", "Attempt to Murder"}
_BNSS_DEFAULT_DEADLINE_DAYS = 60
_BNSS_GRAVE_DEADLINE_DAYS = 90


@ttl_cached()
def _chargesheeted_case_ids() -> set[str]:
    """Real CaseMasterIDs with an actual chargesheet filed (cstype='A',
    same 'A' = filed / 'B' = false case / 'C' = undetected convention
    already established for this table elsewhere in this app) — a case
    without one of these is still open for the BNSS deadline's purposes,
    regardless of what CaseStatusName happens to say (a case's status field
    and its real ChargesheetDetails rows are two independently-populated
    facts in this dataset, already known to disagree on a real fraction of
    cases — see the Case Outcome Flow finding)."""
    rows = fetch_all_rows("ChargesheetDetails", ["CaseMasterID", "cstype"])
    return {r["CaseMasterID"] for r in rows if r.get("cstype") == "A"}


def get_bnss_deadline_alerts(
    station_ids: list[int] | None = None, within_days: int = 7, limit: int = 25, offset: int = 0,
) -> dict:
    """Real, computed BNSS Section 187 deadline alerts — for every in-scope
    arrest whose case has NO chargesheet filed yet, deadline = arrest_date +
    (90 or 60 days, by crime type). Anything within `within_days` of the
    dataset's own anchor date, OR already past it (a negative days_remaining
    — a real, honestly-surfaced fact about this dataset, not clamped to
    zero), counts as an alert. Sorted most urgent first (most-overdue last,
    soonest-due first — see `approaching`/`overdue` split below).

    This is a static historical dataset with no live chargesheet-filing
    process behind it, so the honest result skews heavily overdue rather
    than "a few items due this week" (live-verified: 1,206 of 1,210 total
    matches are already past their deadline, only 4 fall inside the actual
    0-7-day `approaching` window) — both counts are returned separately so
    a notification badge can lead with the meaningful "4 approaching"
    number instead of an alarming, not-very-actionable 1,210."""
    anchor = datetime.strptime(_anchor_date(), "%Y-%m-%d")
    chargesheeted = _chargesheeted_case_ids()
    rows = _scoped(_all_arrests_enriched(), station_ids)

    alerts = []
    for r in rows:
        if not r.get("arrest_date") or r.get("case_master_id") in chargesheeted:
            continue
        try:
            arrest_dt = datetime.strptime(str(r["arrest_date"])[:10], "%Y-%m-%d")
        except ValueError:
            continue
        deadline_days = _BNSS_GRAVE_DEADLINE_DAYS if r["crime_type"] in _BNSS_90_DAY_CRIME_TYPES else _BNSS_DEFAULT_DEADLINE_DAYS
        deadline_dt = arrest_dt + timedelta(days=deadline_days)
        days_remaining = (deadline_dt - anchor).days
        if days_remaining > within_days:
            continue
        alerts.append({
            "arrest_surrender_id": r["arrest_surrender_id"],
            "crime_no": r["crime_no"],
            "accused_name": r["accused_name"],
            "crime_type": r["crime_type"],
            "arrest_date": r["arrest_date"],
            "deadline_date": deadline_dt.strftime("%Y-%m-%d"),
            "deadline_days": deadline_days,
            "days_remaining": days_remaining,
            "overdue": days_remaining < 0,
        })

    # Urgency order, not literal chronological order: every item still due
    # (days_remaining >= 0) sorts first, soonest first; everything already
    # overdue sorts after, MOST RECENTLY crossed first. A plain ascending
    # sort on days_remaining would put the oldest, least-actionable 2018
    # backlog at the very top of a length-limited list (live-verified: only
    # 4 of 1,210 real matches are still within the actual 0-7-day window,
    # so a naive sort+limit gives back 10 items that are all years stale)
    # — this ordering keeps a capped list (e.g. a notification dropdown)
    # actually useful.
    alerts.sort(key=lambda a: a["days_remaining"] if a["days_remaining"] >= 0 else -a["days_remaining"] + 100_000)
    approaching_count = sum(1 for a in alerts if not a["overdue"])
    overdue_count = len(alerts) - approaching_count
    return {
        "total": len(alerts),
        "approaching_count": approaching_count,
        "overdue_count": overdue_count,
        "anchor_date": _anchor_date(),
        "alerts": alerts[offset:offset + limit],
    }


def get_custody_list(
    station_ids: list[int] | None = None, in_custody_only: bool = False,
    police_station_id: int | None = None, bail_status: str | None = None,
    crime_type: str | None = None, name: str | None = None,
    from_date: str | None = None, to_date: str | None = None,
    limit: int = 25, offset: int = 0,
) -> dict:
    """Real, paginated arrest list for the registry's main table — most
    recent arrest first. police_station_id is an additional caller-chosen
    narrowing on top of the jurisdiction scope (same two-filter contract as
    services/db_service.search_cases's own station_ids + police_station_id)
    — never a way to see outside station_ids, only to narrow within it.

    bail_status/crime_type/name/from_date/to_date added 2026-08-27 for the
    Custody Registry page's filter row + accused-name search — all filter
    the same already-enriched, already-cached rows in memory (1,500 rows
    total, comfortably filterable in Python; no new query pattern). name is
    a case-insensitive substring match, same UX as every other free-text
    name search in this app (see db_service.get_accused_history)."""
    rows = _scoped(_all_arrests_enriched(), station_ids)
    if police_station_id is not None:
        rows = [r for r in rows if _is_int_in(r.get("police_station_id"), {police_station_id})]
    if in_custody_only:
        rows = [r for r in rows if r["in_custody"]]
    if bail_status:
        rows = [r for r in rows if r.get("bail_status") == bail_status]
    if crime_type:
        rows = [r for r in rows if r.get("crime_type") == crime_type]
    if name:
        needle = name.strip().lower()
        rows = [r for r in rows if needle in (r.get("accused_name") or "").lower()]
    if from_date:
        rows = [r for r in rows if r.get("arrest_date") and str(r["arrest_date"])[:10] >= from_date]
    if to_date:
        rows = [r for r in rows if r.get("arrest_date") and str(r["arrest_date"])[:10] <= to_date]
    rows = sorted(rows, key=lambda r: r.get("arrest_date") or "", reverse=True)
    return {"total": len(rows), "arrests": rows[offset:offset + limit]}


def get_denied_bail_crime_type_breakdown(station_ids: list[int] | None = None) -> list[dict]:
    """Real crime-type breakdown for Denied-bail arrests specifically —
    added 2026-08-27 for the Custody Registry summary cards. bail_status_
    breakdown in get_custody_summary already gives the overall Granted/
    Pending/Denied split; this crosses just the Denied slice with crime_type,
    a second, cheap pass over the same cached rows."""
    rows = _scoped(_all_arrests_enriched(), station_ids)
    counts: dict[str, int] = {}
    for r in rows:
        if r.get("bail_status") != "Denied":
            continue
        ct = r.get("crime_type") or "Unspecified"
        counts[ct] = counts.get(ct, 0) + 1
    return sorted(
        [{"crime_type": ct, "count": n} for ct, n in counts.items()],
        key=lambda x: x["count"], reverse=True,
    )
