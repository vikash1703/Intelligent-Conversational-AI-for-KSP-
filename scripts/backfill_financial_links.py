"""
One-time data-linking pass for FinancialTransaction.accused_id/case_master_id/
district_id, in the same spirit as scripts/backfill_links.py and
scripts/backfill_network_ids.py.

Live-verified: all 2000 FinancialTransaction rows share the exact same
placeholder accused_id/case_master_id/district_id value ('4343700000') — a
10-digit stand-in that doesn't match any real Accused, CaseMaster, or
District ROWID (those run 17, 17, and 17 digits respectively in this Data
Store). This is the same class of gap backfill_links.py already fixed for
Victim/ComplainantDetails/ArrestSurrender/ChargesheetDetails: the FK column
exists but is 100% unpopulated with anything real, not partially linked.

This does NOT invent new transactions or fabricate financial facts — every
row's own data (amount, transaction_type, is_suspicious) already exists and
is left untouched. This only assigns each row a real, valid Accused (and
that same accused's own real CaseMasterID, so the transaction's case
reference stays internally consistent with who it's tied to) and a real
District, so a Network-graph financial layer has real linked entities to
draw from instead of every transaction pointing at the same placeholder id.
Assignment is randomized-but-valid (fixed seed for reproducibility) — the
same honest tradeoff already used twice in this exact dataset, not real
financial-crime records with a true, discoverable link to preserve.

Usage: python scripts/backfill_financial_links.py
"""
import sys
import os
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.catalyst_client import execute_zcql, update_rows

random.seed(42)
BATCH_SIZE = 50
PAGE_SIZE = 300  # ZCQL's hard per-query LIMIT ceiling


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
    print("Fetching real Accused rows (ROWID + their own CaseMasterID)...")
    accused_rows = fetch_all("Accused", "CaseMasterID")
    print(f"  {len(accused_rows)} accused available to link against")

    print("Fetching real District rows...")
    district_ids = [r["ROWID"] for r in fetch_all("District")]
    print(f"  {len(district_ids)} districts available to link against")

    print("Fetching FinancialTransaction rows...")
    txn_rows = fetch_all("FinancialTransaction")
    print(f"  {len(txn_rows)} transactions to relink")

    updates = []
    for txn in txn_rows:
        accused = random.choice(accused_rows)
        updates.append({
            "ROWID": int(txn["ROWID"]),
            "accused_id": accused["ROWID"],
            "case_master_id": accused["CaseMasterID"],
            "district_id": random.choice(district_ids),
        })

    print(f"Updating {len(updates)} FinancialTransaction rows...")
    batched_update("FinancialTransaction", updates)
    print("Done.")


if __name__ == "__main__":
    main()
