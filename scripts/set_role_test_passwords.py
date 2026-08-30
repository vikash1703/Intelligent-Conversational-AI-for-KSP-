"""One-off: sets a fixed, easy-to-share password on the 4 real-rank test
accounts used by the login page's Quick Role Access cards (2026-08-26) — a
demo convenience, not a production password policy.

Usage: python scripts/set_role_test_passwords.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.catalyst_client import execute_zcql, update_rows, zcql_escape
from core.security import hash_password

USERNAMES = ["DGPTEST", "IGPTEST", "SPTEST", "INSPECTORTEST"]
NEW_PASSWORD = "1234"


def main():
    new_hash = hash_password(NEW_PASSWORD)
    for username in USERNAMES:
        rows = execute_zcql(
            f"SELECT ROWID, Username FROM AppUser WHERE Username = '{zcql_escape(username)}'"
        )
        if not rows:
            print(f"SKIP {username}: no AppUser row found")
            continue
        row_id = rows[0]["AppUser"]["ROWID"]
        update_rows("AppUser", [{"ROWID": row_id, "PasswordHash": new_hash}])
        print(f"OK {username}: password set to '{NEW_PASSWORD}'")


if __name__ == "__main__":
    main()
