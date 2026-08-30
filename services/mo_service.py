import logging
import math
from datetime import datetime

from core.catalyst_client import execute_zcql, zcql_escape, fetch_all_rows
from services.analytics_service import extract_crime_type

logger = logging.getLogger("MOService")

# MO signature = crime_type, derived from BriefFacts (see analytics_service module
# docstring for why). Act/Section codes were evaluated as a richer per-case MO
# fingerprint and dropped: live-verified that ActSectionAssociation.CaseMasterID is a
# broken FK on this dataset — its values are small sequential ints (361-2214 sampled)
# that don't correspond to any CaseMaster.ROWID (which lives in Catalyst's own
# 43437000000xxxxxx ROWID space) — an orphaned reference, not just sparse/NULL like
# the other documented FK gaps. So the only real MO signal available is crime_type
# itself; what this module adds beyond that is temporal clustering (does the same
# crime_type repeat in a tight time+distance window, suggesting a spree/series),
# which similarity_service's case-similarity search doesn't consider at all.
_CANDIDATE_PAGE_SIZE = 300  # ZCQL's hard per-query LIMIT ceiling
# REAL BUG FIXED 2026-08-23 (codebase-wide pagination audit): this used to be a
# single LIMIT 300 query — meaning series/spree detection only ever considered
# an arbitrary first-300-in-ZCQL-order subset of a crime type's real ~700-800
# cases, silently missing genuine clustered matches beyond that. Fixed by
# paginating through every same-crime_type candidate, same as
# similarity_service.find_similar_cases' identical fix.
_CANDIDATE_MAX_PAGES = 10
_SERIES_DAY_WINDOW = 30      # cases within this many days of each other read as a spree
_SERIES_DISTANCE_KM = 15     # ...and within this radius
_SERIES_MIN_MATCHES = 3      # this many clustered matches before calling it a "Possible Series"


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Flat-earth approximation — adequate at Karnataka-state scale, same shape as
    similarity_service's distance calc (kept local rather than imported since that
    one is module-private there)."""
    return math.sqrt((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2) * 111


def analyze_mo(crime_no: str) -> dict | None:
    """Finds other cases sharing this case's crime_type, then checks whether those
    matches cluster tightly enough in time and space to suggest a repeat pattern — a
    spree/series — rather than coincidence."""
    safe_crime_no = zcql_escape(crime_no)
    target_rows = execute_zcql(
        "SELECT CaseMaster.ROWID, CaseMaster.BriefFacts, CaseMaster.CrimeRegisteredDate, "
        "CaseMaster.latitude, CaseMaster.longitude "
        f"FROM CaseMaster WHERE CaseMaster.CrimeNo = '{safe_crime_no}'"
    )
    if not target_rows:
        return None

    target = target_rows[0].get("CaseMaster", target_rows[0])
    crime_type = extract_crime_type(target.get("BriefFacts"))
    target_date = target.get("CrimeRegisteredDate")
    target_lat, target_lon = target.get("latitude"), target.get("longitude")

    # Every candidate, not just the first 300 (see _CANDIDATE_MAX_PAGES
    # comment above for the live-reproduced ZCQL finding this guards
    # against).
    candidates = fetch_all_rows(
        "CaseMaster", ["CrimeNo", "CrimeRegisteredDate", "latitude", "longitude"],
        f" WHERE CaseMaster.BriefFacts = '{zcql_escape(target.get('BriefFacts', ''))}' "
        f"AND CaseMaster.CrimeNo != '{safe_crime_no}'",
        page_size=_CANDIDATE_PAGE_SIZE, max_pages=_CANDIDATE_MAX_PAGES,
    )

    matches = []
    for row in candidates:
        distance = None
        if row.get("latitude") is not None and target_lat is not None:
            distance = round(_distance_km(float(target_lat), float(target_lon), float(row["latitude"]), float(row["longitude"])), 1)

        days_apart = None
        if row.get("CrimeRegisteredDate") and target_date:
            try:
                d1 = datetime.strptime(str(target_date)[:10], "%Y-%m-%d")
                d2 = datetime.strptime(str(row["CrimeRegisteredDate"])[:10], "%Y-%m-%d")
                days_apart = abs((d1 - d2).days)
            except ValueError:
                pass

        matches.append({"crime_no": row["CrimeNo"], "distance_km": distance, "days_apart": days_apart})

    clustered = [
        m for m in matches
        if m["distance_km"] is not None and m["days_apart"] is not None
        and m["distance_km"] <= _SERIES_DISTANCE_KM and m["days_apart"] <= _SERIES_DAY_WINDOW
    ]
    clustered.sort(key=lambda m: (m["days_apart"], m["distance_km"]))

    # The target case's own coordinates double as the cluster's map center —
    # clustered_matches is already filtered to within _SERIES_DISTANCE_KM of
    # this point, so it's a reasonable place to zoom the Hotspot Map to.
    # Only meaningful (and only sent) when there's an actual cluster to show.
    cluster_center = None
    if clustered and target_lat is not None and target_lon is not None:
        cluster_center = {"latitude": float(target_lat), "longitude": float(target_lon)}

    return {
        "crime_no": crime_no,
        "crime_type": crime_type,
        "mo_basis": "crime_type only — Act/Section codes excluded, see module docstring",
        "total_same_type_cases": len(matches),
        "clustered_matches": clustered[:10],
        "is_possible_series": len(clustered) >= _SERIES_MIN_MATCHES,
        "cluster_center": cluster_center,
        "series_criteria": {
            "within_days": _SERIES_DAY_WINDOW,
            "within_km": _SERIES_DISTANCE_KM,
            "min_matches": _SERIES_MIN_MATCHES,
        },
    }
