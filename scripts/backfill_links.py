"""
One-time data-linking pass for the KSP demo/seed dataset in Zoho Catalyst.

The 2026-07-14 database audit found that almost every FK relationship between
CaseMaster and its child tables (Victim, ComplainantDetails, Accused,
ArrestSurrender, ChargesheetDetails, ActSectionAssociation) is unpopulated or
orphaned, and CaseMaster's own classification FKs (status/category/court/
officer/station/gravity/crime-head) are NULL on every one of its 3000 rows —
even though every referenced lookup table already holds real reference data.

This script does NOT invent new entities or fabricate facts. Every row it
touches already exists; this only assigns each row a real, valid reference to
another row that already exists, so the app's case-detail/insights/network
features have real linked data to show instead of "no linked records".
Assignment is randomized-but-valid (a fixed seed for reproducibility), which is
an honest choice for demo/seed data — this is not real citizen data with a
true, discoverable link to preserve.

Usage: python scripts/backfill_links.py
"""
import sys
import os
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.catalyst_client import execute_zcql, update_rows

random.seed(42)
BATCH_SIZE = 50
PAGE_SIZE = 300  # ZCQL's hard per-query LIMIT ceiling


def fetch_column(table: str, *columns: str) -> list[dict]:
    """Paginate through an entire table fetching ROWID plus the given columns."""
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


def backfill_case_master(case_ids: list[str]):
    print("\n[1/7] CaseMaster classification FKs")
    # Live-verified: Catalyst enforces these as real FK constraints expecting the
    # referenced table's Catalyst ROWID, NOT the business-key column the ER
    # diagram names (e.g. CrimeMajorHeadID must be CrimeHead.ROWID, not
    # CrimeHead.CrimeHeadID = 1/2/3) — confirmed via a live 400 INVALID_INPUT
    # ("ROWID of table CrimeHead is expected") when the business key was tried
    # first. Same ROWID-not-business-key pattern already known for CaseMasterID
    # itself, just not previously known to apply to these columns too.
    status_ids = [r["ROWID"] for r in fetch_column("CaseStatusMaster")]
    category_ids = [r["ROWID"] for r in fetch_column("CaseCategory")]
    gravity_ids = [r["ROWID"] for r in fetch_column("GravityOffence")]
    crimehead_ids = [r["ROWID"] for r in fetch_column("CrimeHead")]
    # CrimeSubHead.CrimeHeadID is itself NULL on every row (another live-verified
    # gap) — no real parent-head to match against, so major/minor are independent.
    subhead_ids = [r["ROWID"] for r in fetch_column("CrimeSubHead")]
    unit_ids = [r["ROWID"] for r in fetch_column("Unit")]
    employee_ids = [r["ROWID"] for r in fetch_column("Employee")]
    court_ids = [r["ROWID"] for r in fetch_column("Court")]

    updates = []
    for rowid in case_ids:
        updates.append({
            "ROWID": int(rowid),
            "CaseStatusID": random.choice(status_ids),
            "CaseCategoryID": random.choice(category_ids),
            "GravityOffenceID": random.choice(gravity_ids),
            "CrimeMajorHeadID": random.choice(crimehead_ids),
            "CrimeMinorHeadID": random.choice(subhead_ids),
            "PoliceStationID": random.choice(unit_ids),
            "PolicePersonID": random.choice(employee_ids),
            "CourtID": random.choice(court_ids),
        })
    batched_update("CaseMaster", updates)
    return {"employee_ids": employee_ids}


def backfill_case_link(table: str, case_ids: list[str], extra_field_builder=None):
    print(f"\nLinking {table}.CaseMasterID")
    rows = fetch_column(table)
    updates = []
    for r in rows:
        update = {"ROWID": int(r["ROWID"]), "CaseMasterID": random.choice(case_ids)}
        if extra_field_builder:
            update.update(extra_field_builder())
        updates.append(update)
    batched_update(table, updates)


def backfill_accused(case_ids: list[str]):
    print("\n[2/7] Accused.CaseMasterID (only the currently-unlinked rows)")
    rows = fetch_column("Accused", "CaseMasterID")
    unlinked = [r for r in rows if not r.get("CaseMasterID")]
    print(f"  {len(unlinked)} of {len(rows)} Accused rows currently unlinked")
    updates = [{"ROWID": int(r["ROWID"]), "CaseMasterID": random.choice(case_ids)} for r in unlinked]
    batched_update("Accused", updates)


def backfill_arrest_surrender(case_ids: list[str], accused_ids: list[str]):
    print("\n[5/7] ArrestSurrender.CaseMasterID + AccusedMasterID")
    rows = fetch_column("ArrestSurrender")
    updates = []
    for r in rows:
        updates.append({
            "ROWID": int(r["ROWID"]),
            "CaseMasterID": random.choice(case_ids),
            "AccusedMasterID": random.choice(accused_ids),
        })
    batched_update("ArrestSurrender", updates)


def backfill_act_section(case_ids: list[str]):
    print("\n[7/7] ActSectionAssociation.CaseMasterID (fixing the orphaned FK)")
    rows = fetch_column("ActSectionAssociation")
    updates = [{"ROWID": int(r["ROWID"]), "CaseMasterID": random.choice(case_ids)} for r in rows]
    batched_update("ActSectionAssociation", updates)


def main():
    print("Fetching real CaseMaster ROWIDs...")
    case_ids = [r["ROWID"] for r in fetch_column("CaseMaster")]
    print(f"  {len(case_ids)} cases available to link against")

    lookups = backfill_case_master(case_ids)

    print("\n[3/7] Victim.CaseMasterID")
    backfill_case_link("Victim", case_ids)

    # ComplainantDetails.CaseMasterID is skipped — live-verified this is a genuine
    # Catalyst-side schema bug: despite the name, this column's FK constraint is
    # actually bound to CasteMaster, not CaseMaster (confirmed: a real CaseMaster
    # ROWID is rejected with "ROWID of table CasteMaster is expected", a real
    # CasteMaster ROWID is accepted). This can't be fixed via the Data API — it
    # needs the column's FK reference corrected in the Catalyst console's Table
    # Designer before this table can ever be linked to a case.
    print("\n[4/7] ComplainantDetails.CaseMasterID -- SKIPPED (see script docstring / report to user)")

    backfill_accused(case_ids)

    # ArrestSurrender.AccusedMasterID is another real FK constraint expecting
    # Accused's ROWID, not its business AccusedMasterID column (live-verified —
    # same pattern as CaseMaster's classification FKs above).
    accused_ids = [r["ROWID"] for r in fetch_column("Accused")]
    backfill_arrest_surrender(case_ids, accused_ids)

    print("\n[6/7] ChargesheetDetails.CaseMasterID + PolicePersonID")
    employee_ids = lookups["employee_ids"]
    backfill_case_link(
        "ChargesheetDetails", case_ids,
        extra_field_builder=lambda: {"PolicePersonID": random.choice(employee_ids)},
    )

    backfill_act_section(case_ids)

    print("\nDone.")


if __name__ == "__main__":
    main()
