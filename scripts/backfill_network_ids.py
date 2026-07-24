"""
One-time data-linking pass for CriminalNetwork.accused_id (the Network page's
graph data), in the same spirit as scripts/backfill_links.py.

Audited live (2026-07-15): every one of CriminalNetwork's 300 rows has its own
"owner" accused_id set to a ~17-digit ROWID-shaped value that doesn't match any
real Accused row — this is why clicking most nodes in the Network graph hit
"unresolved ID" instead of a real profile. Tried remapping these to real
Accused.AccusedMasterID business-key values first; Catalyst's own Update Row
API rejected that with "Invalid Foreign key value for column accused_id. ROWID
of table Accused is expected" — meaning accused_id is a *real* FK straight to
Accused.ROWID, not to Accused.AccusedMasterID as the ER diagram's column name
implies. services/network_service.get_accused_profile has been updated to
match: it now resolves large (ROWID-shaped) ids via Accused.ROWID and small
(business-key-shaped) ids via Accused.AccusedMasterID, auto-detected purely by
magnitude since the two id spaces never numerically overlap.

The *other* 445 ids that show up only inside connections_json (never as an
owner) are, by contrast, plain AccusedMasterID business keys already in real
range — confirmed no cross-owner references exist between rows, so
connections_json itself needs no rewriting, only each row's own accused_id
column.

This does NOT invent new people — every accused_id this assigns is a real,
existing Accused row's own ROWID, and it's deliberately drawn only from
Accused rows whose AccusedMasterID isn't already sitting inside some other
row's connections_json — so a remapped owner node and an existing connection
node never end up silently representing the same real person as two separate
graph nodes. Randomized-but-valid assignment (fixed seed), same honest
tradeoff as the original backfill for this same demo/seed dataset — not real
citizens with a true link to preserve.

Usage: python scripts/backfill_network_ids.py
"""
import sys
import os
import json
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.catalyst_client import execute_zcql, update_rows

random.seed(42)
BATCH_SIZE = 50
PAGE_SIZE = 300  # ZCQL's hard per-query LIMIT ceiling
_MAX_PLAUSIBLE_ACCUSED_ID = 10_000_000  # same threshold network_service.py uses


def fetch_all(table: str, *columns: str) -> list[dict]:
    """Paginate through an entire table — a bare SELECT with no LIMIT only
    returns Zoho's default page, not every row (live-verified: silently
    capped the Accused fetch below at far fewer than its real 3915 rows)."""
    col_sql = ", ".join(f"{table}.{c}" for c in columns)
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
    print("[1/4] Fetching CriminalNetwork rows...")
    rows = fetch_all("CriminalNetwork", "ROWID", "accused_id", "connections_json")
    print(f"  {len(rows)} rows")

    print("[2/4] Computing which real AccusedMasterIDs are already used as connections...")
    used_master_ids = set()
    for row in rows:
        conns = json.loads(row.get("connections_json") or "{}").get("connected_accused_ids", [])
        for cid in conns:
            used_master_ids.add(str(cid))

    to_fix = [row for row in rows if int(row["accused_id"]) > _MAX_PLAUSIBLE_ACCUSED_ID]
    already_ok = len(rows) - len(to_fix)
    print(f"  {len(used_master_ids)} distinct AccusedMasterIDs already referenced as connections")
    print(f"  {already_ok} rows already have a real owner id (left untouched)")
    print(f"  {len(to_fix)} rows need remapping")

    if not to_fix:
        print("Nothing to do.")
        return

    print("[3/4] Fetching real Accused ROWIDs not already used as a connection...")
    accused_rows = fetch_all("Accused", "ROWID", "AccusedMasterID")
    print(f"  {len(accused_rows)} total Accused rows")
    free_pool = [
        r["ROWID"] for r in accused_rows
        if str(r["AccusedMasterID"]) not in used_master_ids
    ]
    print(f"  {len(free_pool)} free Accused rows available for {len(to_fix)} rows needing one")
    random.shuffle(free_pool)
    if len(free_pool) < len(to_fix):
        print(f"ERROR: only {len(free_pool)} free Accused rows available for {len(to_fix)} rows needing one.")
        sys.exit(1)

    updates = [
        {"ROWID": int(row["ROWID"]), "accused_id": int(free_pool[i])}
        for i, row in enumerate(to_fix)
    ]

    print("[4/4] Writing new owner ids...")
    batched_update("CriminalNetwork", updates)
    print(f"\nDone — {len(updates)} CriminalNetwork rows now point at a real, resolvable Accused record.")


if __name__ == "__main__":
    main()
