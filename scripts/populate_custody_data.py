"""
Populates ArrestSurrender's custody-lifecycle columns (release_date,
bail_status, bail_amount, custody_type) — Tier 1 item 9 (Custody Registry),
2026-08-24.

REAL BUG FOUND BEFORE THIS SCRIPT WAS WRITTEN: the user reported adding 5
columns to ArrestSurrender via the Catalyst console — release_date,
bail_status, bail_amount, custody_type, next_hearing_date. Live-verified via
the Table Management API (scripts/verify_schema.py's list_tables/
list_columns) that only 4 of the 5 actually exist on the live table;
next_hearing_date is not present at all. All 4 real columns are 100% NULL
across all 1,500 real ArrestSurrender rows (full-table check, not a sample).
next_hearing_date is therefore NOT populated by this script — it doesn't
exist to write into. services/custody_service.py computes it as a
deterministic, seeded, non-persisted value instead (same seeding approach as
below, so it's reproducible from the same inputs without needing a real
column) — see that module's own docstring.

This does NOT invent new arrest facts — every row's real ArrestSurrenderDate
and its real linked case (crime type, via CaseMasterID -> CaseMaster.
BriefFacts) are used as-is. Release/bail/custody outcomes are randomized-but-
internally-consistent (release_date always after arrest_date; bail_amount
scaled to the real crime type; a fixed seed so this is reproducible, not a
one-off), the same honest tradeoff already used in scripts/
backfill_financial_links.py and scripts/backfill_network_ids.py for this
project's other simulated-but-plausible fields.

Distribution (documented, not hidden):
  60% released (bail_status="Granted", release_date = arrest_date + 5-180
      real days, bail_amount set)
  25% still in custody, bail "Pending" (bail_amount set, no release_date)
  15% still in custody, bail "Denied" (no bail_amount, no release_date)
  custody_type: 80% Judicial / 20% Police, independent of the above (Police
      custody is realistically brief/initial; Judicial is the long-term
      default) — assigned regardless of release status since custody_type
      describes where time was served, not whether it has ended.

bail_amount ranges (₹, scaled to real crime severity — same crime-type
categories this project already extracts from BriefFacts elsewhere):
  Murder / Attempt to Murder: 50,000 - 500,000
  Online Fraud:                20,000 - 150,000
  Theft:                        5,000 -  50,000

Every screen that displays these 4 fields carries a permanent, visible
"simulated data" disclosure — see frontend/src/pages/CustodyRegistry.jsx and
Cases.jsx's arrest card — this script's job is only to make that disclosed
simulation internally consistent, not to hide that it is one.

Usage: python scripts/populate_custody_data.py
"""
import sys
import os
import random
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.catalyst_client import execute_zcql, update_rows
from services.analytics_service import extract_crime_type

random.seed(42)
BATCH_SIZE = 50
PAGE_SIZE = 300

BAIL_RANGES = {
    "Murder": (50_000, 500_000),
    "Attempt to Murder": (50_000, 500_000),
    "Online Fraud": (20_000, 150_000),
    "Theft": (5_000, 50_000),
}
_DEFAULT_BAIL_RANGE = (10_000, 100_000)


def fetch_all(table: str, *columns: str) -> list[dict]:
    col_sql = ", ".join(f"{table}.{c}" for c in ("ROWID",) + columns)
    all_rows = []
    offset = 0
    while True:
        rows = execute_zcql(f"SELECT {col_sql} FROM {table} LIMIT {offset},{PAGE_SIZE}")
        if not rows:
            break
        all_rows.extend(r.get(table, r) for r in rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return all_rows


def batched_update(table: str, rows: list[dict]):
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i:i + BATCH_SIZE]
        update_rows(table, chunk)
        print(f"  {table}: {min(i + BATCH_SIZE, len(rows))}/{len(rows)}")


def main():
    print("Fetching real CaseMaster rows (for crime type per case)...")
    cases = fetch_all("CaseMaster", "BriefFacts")
    crime_type_by_case = {c["ROWID"]: extract_crime_type(c.get("BriefFacts")) for c in cases}
    print(f"  {len(crime_type_by_case)} cases")

    print("Fetching real ArrestSurrender rows...")
    arrests = fetch_all("ArrestSurrender", "ArrestSurrenderDate", "CaseMasterID")
    print(f"  {len(arrests)} arrest records")

    updates = []
    counts = {"Granted": 0, "Pending": 0, "Denied": 0}
    for a in arrests:
        arrest_date_raw = a.get("ArrestSurrenderDate")
        crime_type = crime_type_by_case.get(a.get("CaseMasterID"))
        lo, hi = BAIL_RANGES.get(crime_type, _DEFAULT_BAIL_RANGE)

        roll = random.random()
        custody_type = "Judicial" if random.random() < 0.8 else "Police"

        if roll < 0.60:
            bail_status = "Granted"
            counts["Granted"] += 1
            bail_amount = random.randint(lo, hi)
            release_date = None
            if arrest_date_raw:
                try:
                    arrest_date = datetime.strptime(str(arrest_date_raw)[:10], "%Y-%m-%d")
                    release_date = (arrest_date + timedelta(days=random.randint(5, 180))).strftime("%Y-%m-%d")
                except ValueError:
                    pass
        elif roll < 0.85:
            bail_status = "Pending"
            counts["Pending"] += 1
            bail_amount = random.randint(lo, hi)
            release_date = None
        else:
            bail_status = "Denied"
            counts["Denied"] += 1
            bail_amount = None
            release_date = None

        updates.append({
            "ROWID": int(a["ROWID"]),
            "release_date": release_date,
            "bail_status": bail_status,
            "bail_amount": bail_amount,
            "custody_type": custody_type,
        })

    print(f"Distribution: {counts}")
    print(f"Updating {len(updates)} ArrestSurrender rows...")
    batched_update("ArrestSurrender", updates)
    print("Done.")


if __name__ == "__main__":
    main()
