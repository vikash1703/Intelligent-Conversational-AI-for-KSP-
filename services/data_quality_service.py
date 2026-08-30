import logging
from datetime import datetime

from core.catalyst_client import fetch_all_rows
from core.ttl_cache import ttl_cached
from services.analytics_service import extract_crime_type
from services.db_service import _resolve_act_sections

logger = logging.getLogger("DataQualityService")

# Real IPC sections that plausibly support each of the dataset's 4 real
# crime_type values (see extract_crime_type) — added 2026-08-24, status-
# contradiction investigation (case 100091036201900002: labeled "Murder" via
# BriefFacts, but its only real linked sections are IPC 307/379, no 302 at
# all). crime_type and ActSectionAssociation are two completely independent
# fields with nothing in the schema or app enforcing agreement between them.
# Deliberately generous (386/419/420/406 all count as "Fraud", 378 counts
# alongside 379 for "Theft", IT Act sections count for Online Fraud too) so
# this flags only real, unambiguous disagreement, not pedantic section-code
# quibbling.
_EXPECTED_IPC_SECTIONS = {
    "Murder": {"302"},
    "Attempt to Murder": {"307"},
    "Theft": {"378", "379"},
    "Online Fraud": {"419", "420", "406"},
}
_ONLINE_FRAUD_EXTRA_ACTS = {"IT"}

# Dataset-wide generalization of the per-case "Incident date is N days after
# the registered date" flag Cases.jsx already shows (see that file's
# reportingDelayNote) — added 2026-08-23, Tier 1 item 4. Every category here
# is a REAL, live-computed count over the full ~3,000-row CaseMaster table
# (and its real child tables), never an estimate — this whole page exists to
# be the honest explanation for why some analyses return fewer results than
# expected, so it would defeat its own purpose to guess at its own numbers.


@ttl_cached()
def _all_cases() -> dict[str, dict]:
    """Every real CaseMaster row, keyed by ROWID.

    UPGRADED 2026-08-23 (codebase-wide pagination audit) to cursor-based
    pagination (core.catalyst_client.fetch_all_rows) — the original ROWID-
    dedup fix (from the Tier 1 item 2 finding this comment used to cite)
    stopped double-counting but NOT the matching failure mode: a duplicate
    silently displaces a different real row, so dedup-on-receipt alone can
    still under-count a table (measured directly on this exact table: offset+
    dedup returned 2999 of 3000 real cases). This matters more here than
    almost anywhere else in the app — an undercount would make this data-
    quality page's own numbers wrong, which is precisely the one page that
    can least afford that. Cached: get_quality_summary() calls this on every
    request (for total_cases), separately from _compute_all_issues()'s own
    cached call to it — without this, a warm-cache summary call still paid
    the full CaseMaster scan just to recount rows already cached elsewhere
    (measured live 2026-08-23)."""
    rows = fetch_all_rows(
        "CaseMaster", ["CrimeNo", "CrimeRegisteredDate", "IncidentFromDate", "PoliceStationID", "BriefFacts"],
    )
    return {row["ROWID"]: row for row in rows}


def _case_ids_with_rows(table: str) -> set[str]:
    """Distinct CaseMasterID values with at least one row in `table`.

    UPGRADED 2026-08-23 (codebase-wide pagination audit) to cursor-based
    pagination — a set already absorbs a plain DUPLICATE row fine, but not
    the more serious failure mode: offset pagination can silently DROP a real
    row when a duplicate elsewhere displaces it (see _all_cases' updated
    docstring for the direct proof), which would make a case with a real
    victim/accused/act-section wrongly show up in this page's own "no
    linked X" count — the exact kind of false positive this page exists to
    prevent, not cause."""
    rows = fetch_all_rows(table, ["CaseMasterID"])
    ids = {row.get("CaseMasterID") for row in rows}
    return ids


def _case_act_sections() -> dict[str, set[tuple[str, str]]]:
    """Every case's real, resolved (ActCode, SectionCode) pairs — resolved via
    the SAME canonical services.db_service._resolve_act_sections function
    case-detail/timeline use, not a second independent interpretation of the
    raw ROWID-or-business-key-mixed column (see that function's own
    docstring). max_pages=40, not the fetch_all_rows default of 30 — the real
    table has 9,022 rows (31 pages of 300), and the default cap would have
    silently truncated this exact scan by 22 rows (caught live 2026-08-24
    while building this)."""
    rows = fetch_all_rows("ActSectionAssociation", ["CaseMasterID", "ActCode", "SectionCode"], max_pages=40)
    resolved = _resolve_act_sections(rows)
    by_case: dict[str, set[tuple[str, str]]] = {}
    for r in resolved:
        by_case.setdefault(r["CaseMasterID"], set()).add(((r.get("ActCode") or "").upper(), (r.get("SectionCode") or "").strip()))
    return by_case


def _crime_type_matches_sections(crime_type: str, sections: set[tuple[str, str]]) -> bool:
    expected = _EXPECTED_IPC_SECTIONS.get(crime_type)
    if expected is None:
        return True  # "Unspecified" or any future crime_type — nothing to check against
    if any(act == "IPC" and sec in expected for act, sec in sections):
        return True
    if crime_type == "Online Fraud" and any(act in _ONLINE_FRAUD_EXTRA_ACTS for act, _ in sections):
        return True
    return False


@ttl_cached()
def _compute_all_issues() -> dict[str, list[dict]]:
    """One full scan producing every category's affected-case list at once
    (each category reuses the same _all_cases() fetch rather than 4 separate
    full scans) — TTL-cached since none of this changes mid-demo/session."""
    cases = _all_cases()
    victim_ids = _case_ids_with_rows("Victim")
    accused_ids = _case_ids_with_rows("Accused")
    # Real bug fixed 2026-08-26: this used to be a SEPARATE fetch_all_rows
    # scan of the same 9,022-row ActSectionAssociation table (via
    # _case_ids_with_rows' default max_pages=30, an undercount — see
    # _case_act_sections' own docstring on why this table specifically needs
    # max_pages=40). Redundant AND wrong: sections_by_case already carries
    # every case id with a real section row (_resolve_act_sections never
    # drops rows, only relabels them), so deriving section_ids from it
    # instead removes a full duplicate table scan (a real, measured
    # contributor to this endpoint's cold-cache time creeping past the
    # frontend's 30s timeout) and fixes the same 22-row undercount already
    # fixed once at the neighboring call site but missed here.
    sections_by_case = _case_act_sections()
    section_ids = set(sections_by_case.keys())

    date_contradiction, no_victim, no_accused, no_sections = [], [], [], []
    crime_type_mismatch = []
    for rowid, c in cases.items():
        entry = {"crime_no": c.get("CrimeNo"), "case_rowid": rowid, "police_station_id": c.get("PoliceStationID")}
        reg, inc = c.get("CrimeRegisteredDate"), c.get("IncidentFromDate")
        if reg and inc:
            try:
                d_reg = datetime.strptime(str(reg)[:10], "%Y-%m-%d")
                d_inc = datetime.strptime(str(inc)[:10], "%Y-%m-%d")
                delay = (d_reg - d_inc).days
                if delay < 0:
                    date_contradiction.append({
                        **entry, "registered_date": reg, "incident_date": inc, "days_before_registration": -delay,
                    })
            except ValueError:
                pass
        if rowid not in victim_ids:
            no_victim.append(entry)
        if rowid not in accused_ids:
            no_accused.append(entry)
        if rowid not in section_ids:
            no_sections.append(entry)

        crime_type = extract_crime_type(c.get("BriefFacts"))
        sections = sections_by_case.get(rowid, set())
        if not _crime_type_matches_sections(crime_type, sections):
            crime_type_mismatch.append({
                **entry,
                "crime_type": crime_type,
                "linked_sections": sorted(f"{act} {sec}".strip() for act, sec in sections) or ["(none)"],
            })

    return {
        "date_contradiction": date_contradiction,
        "no_victim": no_victim,
        "no_accused": no_accused,
        "no_act_sections": no_sections,
        "crime_type_section_mismatch": crime_type_mismatch,
    }


def _filter_scoped(entries: list[dict], station_ids: list[int] | None) -> list[dict]:
    if station_ids is None:
        return entries
    result = []
    for e in entries:
        try:
            if int(e.get("police_station_id")) in station_ids:
                result.append(e)
        except (TypeError, ValueError):
            continue
    return result


def get_quality_summary(station_ids: list[int] | None = None) -> dict:
    """Counts + percentages for every category, scoped to station_ids like
    every other case-touching endpoint in this app. total_cases itself is
    also scoped, so a percentage always reads against the officer's own
    real denominator, not the statewide one."""
    issues = _compute_all_issues()
    if station_ids is None:
        total = len(_all_cases())
    else:
        station_id_set = set(station_ids)
        total = sum(
            1 for c in _all_cases().values()
            if _is_int_in(c.get("PoliceStationID"), station_id_set)
        )

    categories = {}
    for key, entries in issues.items():
        scoped = _filter_scoped(entries, station_ids)
        categories[key] = {
            "count": len(scoped),
            "pct": round(len(scoped) / total * 100, 1) if total else 0.0,
        }
    return {
        "total_cases": total,
        "categories": categories,
        # Not drill-down categories (nothing to act on) — real, checked
        # facts about this dataset's own repair history, surfaced here since
        # this page is meant to be the honest, complete picture. Both
        # re-confirmed live 2026-08-23:
        "resolved_notes": [
            "ComplainantDetails.CaseMasterID: previously documented as a broken FK bound to the wrong table — re-checked, now fully linked (3,000/3,000 real cases).",
            "13 of 40 police stations had no district assignment ('City Police Station'/'Market Police Station' — names too generic to resolve automatically) — assigned to Bengaluru Urban as an explicit human assertion, not a derived fact (see services/db_service.py's provenance note on Unit.DistrictID).",
        ],
        # Not a per-case gap (nothing to drill into) and not "resolved" either
        # — a real, checked characteristic of the dataset itself, surfaced
        # here for the same reason as everything else on this page: added
        # 2026-08-24 from the Item 8 forecast-method audit (services.
        # forecast_service.get_district_hotspot_forecast's own docstring
        # carries the full finding this is summarizing).
        "dataset_characteristics": [
            "Statewide crime slope is 0.00 cases/month across 96 months. Every district's individual slope is negligible (-0.0095 to +0.0113). A real crime dataset shows seasonal and multi-year movement. This is consistent with the near-even status distribution found in the case outcome analysis — both indicate synthetic data generation without temporal modelling.",
        ],
    }


def _is_int_in(value, id_set: set[int]) -> bool:
    try:
        return int(value) in id_set
    except (TypeError, ValueError):
        return False


def get_quality_drilldown(category: str, station_ids: list[int] | None = None, limit: int = 25, offset: int = 0) -> dict:
    """Paginated real crime-number list for one category — server-side
    pagination, no unbounded returns (some categories affect 900+ real
    cases)."""
    issues = _compute_all_issues()
    entries = issues.get(category)
    if entries is None:
        return {"total": 0, "cases": []}
    scoped = _filter_scoped(entries, station_ids)
    scoped = sorted(scoped, key=lambda e: e.get("crime_no") or "")
    return {"total": len(scoped), "cases": scoped[offset:offset + limit]}
