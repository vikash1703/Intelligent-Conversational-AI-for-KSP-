"""One-off: reset an AppUser's password since the original plaintext was never
persisted anywhere (only its bcrypt hash was written to AppUser at creation
time) — see memory note from 2026-07-24.

Usage: python scripts/reset_ksptest_password.py [USERNAME]
Defaults to KSPTEST if no username is given.
"""
import secrets
import string
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.catalyst_client import execute_zcql, update_rows, zcql_escape
from core.security import hash_password

USERNAME = sys.argv[1] if len(sys.argv) > 1 else "KSPTEST"


def generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pwd) and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd) and any(c in "!@#$%^&*" for c in pwd)):
            return pwd


def main():
    rows = execute_zcql(
        f"SELECT ROWID, Username FROM AppUser WHERE Username = '{zcql_escape(USERNAME)}'"
    )
    if not rows:
        print(f"No AppUser row found for username={USERNAME}")
        sys.exit(1)

    row_id = rows[0]["AppUser"]["ROWID"]
    new_password = generate_password()
    new_hash = hash_password(new_password)

    update_rows("AppUser", [{"ROWID": row_id, "PasswordHash": new_hash}])

    print(f"Username: {USERNAME}")
    print(f"New password: {new_password}")


if __name__ == "__main__":
    main()
