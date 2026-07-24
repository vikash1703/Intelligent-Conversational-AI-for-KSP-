"""
One-time rename pass for Accused.AccusedName, in the same spirit as
scripts/backfill_links.py and scripts/backfill_network_ids.py.

The seed dataset names every Accused row "Accused Person-<n>" — a synthetic
placeholder pattern that's immediately visible as fake data in the UI (search
placeholder text, Network graph labels, Accused History results) and doesn't
behave like a real name search would (no partial matches, no realistic
collisions to handle). This assigns each row a real-sounding Karnataka name
instead, drawn from real Kannada first-name and surname pools, matched to
each row's own recorded GenderID where available.

Every (first, last) pair assigned is UNIQUE across the whole run — built from
the full cross product of the name pools (large enough to cover every live
Accused row with room to spare), shuffled with a fixed seed, then handed out
one at a time with no reuse. This is a deliberate design choice, not just a
low collision probability: this schema has no cross-case person identifier
(Accused.AccusedMasterID is a per-case appearance, not a stable per-human ID —
see services/db_service.py), so AccusedName is the only thing tying one
person's cases together in the Accused History search. If two *different*
people ended up with the exact same name, their case histories would look
merged. Guaranteeing uniqueness here removes that risk for this dataset (the
apps' own grouping logic in api/routers/cases.py additionally keys on
name+age+gender as a second line of defense, in case this script is ever
re-run against already-renamed or hand-edited data where uniqueness can't be
guaranteed the same way).

This does NOT invent new case facts — only the display name on rows that
already existed with a synthetic placeholder name, same honest tradeoff
already established for this demo/seed dataset in the earlier backfill
scripts (not real citizens with a true name to preserve).

Usage: python scripts/backfill_accused_names.py
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
BACKUP_PATH = os.path.join(os.path.dirname(__file__), "accused_name_backup.json")

MALE_FIRST_NAMES = [
    "Manjunath", "Ravindra", "Prakash", "Suresh", "Nagaraj", "Basavaraj", "Girish",
    "Mahesh", "Vijay", "Srinivas", "Shivakumar", "Nataraj", "Ramesh", "Gopal",
    "Krishnamurthy", "Venkatesh", "Raghavendra", "Yogesh", "Dinesh", "Umesh",
    "Mohan", "Anand", "Ganesh", "Ashok", "Vinay", "Santosh", "Rajesh", "Harish",
    "Sunil", "Arun", "Kiran", "Vishwanath", "Chandru", "Ramanna", "Muniraj",
    "Lokesh", "Sathish", "Manohar", "Prabhakar", "Jagadish", "Somashekar",
    "Veeresh", "Parameshwar", "Devendra", "Raghu", "Nanjunda", "Chikkanna",
    "Hanumantha", "Shankarappa", "Puttaswamy", "Byrappa", "Siddappa", "Halappa",
    "Mallikarjuna", "Channabasappa", "Kariyappa",
]
FEMALE_FIRST_NAMES = [
    "Lakshmi", "Sharada", "Kaveri", "Sunanda", "Gayathri", "Vasanthi", "Shobha",
    "Radha", "Savitri", "Parvathi", "Girija", "Kamala", "Vijaya", "Nagaveni",
    "Bhagya", "Roopa", "Manjula", "Kavya", "Anitha", "Sowmya", "Deepa", "Padmini",
    "Shwetha", "Rekha", "Jyothi", "Vidya", "Meena", "Nirmala", "Pushpa", "Yashoda",
    "Chaya", "Sarala", "Prema", "Rathna", "Suma", "Latha", "Bharathi", "Malathi",
    "Renuka", "Uma", "Indira", "Sujatha", "Kalpana", "Shanthi", "Geetha", "Ambika",
    "Leelavathi", "Basamma", "Puttamma", "Channamma",
]
SURNAMES = [
    "Gowda", "Shetty", "Naik", "Hegde", "Poojary", "Bhat", "Patil", "Kulkarni",
    "Achar", "Rao", "Reddy", "Murthy", "Setty", "Nayak", "Prabhu", "Kamath",
    "Pai", "Shenoy", "Kalburgi", "Deshpande", "Joshi", "Naidu", "Iyer", "Iyengar",
    "Urs", "Rai", "Bhandary", "Shastri", "Acharya", "Bhagwat", "Suvarna",
    "Shirodkar", "Kotian", "Salian", "Rathod", "Yadav", "Hombal", "Halagatti",
    "Kavalur", "Belliappa", "Muthanna", "Ponnappa", "Aiyanna", "Devadiga",
    "Bangera", "Mendon", "Sequeira", "D'Souza", "Fernandes", "Pinto", "Colaco",
    "Rasquinha", "Vaz", "Kambli", "Shanbhag", "Kamat", "Nadig", "Bailoor",
    "Padukone", "Chandavarkar", "Gokhale",
]


def unique_pairs(first_names: list[str], last_names: list[str], count: int) -> list[str]:
    """count unique 'First Last' combinations drawn from the full cross
    product, shuffled once with the module's fixed seed — never the same
    pair twice within (or across) calls sharing this seeded RNG state."""
    pool = [f"{f} {l}" for f in first_names for l in last_names]
    random.shuffle(pool)
    if count > len(pool):
        raise ValueError(f"Name pool too small: need {count}, have {len(pool)}")
    return pool[:count]


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
    print("Fetching all Accused rows...")
    rows = fetch_all("Accused", "AccusedName", "GenderID")
    print(f"  {len(rows)} rows total")

    male_rows = [r for r in rows if (r.get("GenderID") or "").upper() == "M"]
    female_rows = [r for r in rows if (r.get("GenderID") or "").upper() == "F"]
    male_ids = {r["ROWID"] for r in male_rows}
    female_ids = {r["ROWID"] for r in female_rows}
    other_rows = [r for r in rows if r["ROWID"] not in male_ids and r["ROWID"] not in female_ids]

    print(f"  male={len(male_rows)} female={len(female_rows)} other/unknown={len(other_rows)}")

    male_names = unique_pairs(MALE_FIRST_NAMES, SURNAMES, len(male_rows))
    female_names = unique_pairs(FEMALE_FIRST_NAMES, SURNAMES, len(female_rows))
    # "other" (T / blank / unrecognized GenderID) draws from the combined
    # first-name pool, filtered against everything already handed out to
    # male_rows/female_rows so the whole run stays globally unique even
    # though this draw isn't from unique_pairs()'s own single shuffled pool.
    used = set(male_names) | set(female_names)
    combined_pool = [f"{f} {l}" for f in MALE_FIRST_NAMES + FEMALE_FIRST_NAMES for l in SURNAMES]
    random.shuffle(combined_pool)
    other_names = [n for n in combined_pool if n not in used][:len(other_rows)]
    if len(other_names) < len(other_rows):
        raise ValueError("Not enough remaining unique names for 'other' gender rows")

    backup = []
    updates = []

    def queue(row, new_name):
        backup.append({"ROWID": row["ROWID"], "old_name": row.get("AccusedName")})
        updates.append({"ROWID": int(row["ROWID"]), "AccusedName": new_name})

    for row, name in zip(male_rows, male_names):
        queue(row, name)
    for row, name in zip(female_rows, female_names):
        queue(row, name)
    for row, name in zip(other_rows, other_names):
        queue(row, name)

    assert len(updates) == len(rows), f"{len(updates)} queued vs {len(rows)} rows"
    assert len({u["AccusedName"] for u in updates}) == len(updates), "duplicate names assigned"

    with open(BACKUP_PATH, "w") as f:
        json.dump(backup, f, indent=2)
    print(f"Backup of {len(backup)} original names written to {BACKUP_PATH}")

    print(f"Updating {len(updates)} Accused rows...")
    batched_update("Accused", updates)
    print("Done.")


if __name__ == "__main__":
    main()
