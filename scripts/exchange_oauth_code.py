"""One-time helper to turn a fresh Zoho grant/authorization code into a new
refresh token, for use after regenerating OAuth scopes in the Zoho API
Console (needed here specifically to add ZohoCatalyst.tables.rows.DELETE,
which the current refresh token doesn't have — live-verified via a 401
OAUTH_SCOPE_MISMATCH on every delete_row() call).

Usage:
    1. Go to https://api-console.zoho.in and open the Self Client already
       used for this project (same one ZOHO_CLIENT_ID/ZOHO_CLIENT_SECRET in
       .env belong to).
    2. Open its "Generate Code" tab. In the Scope field, enter exactly:
       ZohoCatalyst.zcql.CREATE,ZohoCatalyst.tables.rows.CREATE,ZohoCatalyst.tables.rows.READ,ZohoCatalyst.tables.rows.UPDATE,ZohoCatalyst.tables.rows.DELETE,QuickML.deployment.READ
       (all six together, comma-separated, no spaces). This is the FULL list —
       don't drop any of them. Learned the hard way, twice: a first attempt used
       only the four tables.rows.* scopes and broke every read in the app
       (zcql.CREATE isn't implied by row-level CRUD scopes — separate grant for
       the query engine); a second attempt added zcql.CREATE but dropped
       QuickML.deployment.READ, which broke Chat/Insights/Translate (anything
       going through ask_zoho_rag()) with INVALID_OAUTHSCOPE. All six above are
       confirmed against Catalyst's OAuth scopes docs and this app's actual
       usage — nothing else in this codebase calls a Catalyst API outside
       these six scopes.
    3. Pick a time duration (10 minutes is plenty) and click CREATE. Copy the
       code shown — it's only valid for a few minutes and can only be used once.
    4. Run: python scripts/exchange_oauth_code.py PASTE_THE_CODE_HERE
    5. Copy the refresh_token this prints into .env as ZOHO_REFRESH_TOKEN,
       then restart the backend.
"""
import sys
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import requests

from core.config import settings

_ENV_PATH = os.path.join(_ROOT, ".env")


def _write_refresh_token(new_token: str) -> None:
    """Writes the new token straight into .env instead of printing it anywhere —
    a live OAuth credential shouldn't sit in a terminal scrollback/transcript
    when the only thing that ever needs it is this one file."""
    with open(_ENV_PATH) as f:
        content = f.read()
    updated, count = re.subn(
        r"^ZOHO_REFRESH_TOKEN=.*$", f"ZOHO_REFRESH_TOKEN={new_token}", content, flags=re.MULTILINE
    )
    if count == 0:
        raise RuntimeError("Couldn't find a ZOHO_REFRESH_TOKEN= line in .env to replace")
    with open(_ENV_PATH, "w") as f:
        f.write(updated)


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/exchange_oauth_code.py <grant_code>")
        sys.exit(1)

    code = sys.argv[1]
    response = requests.post(
        "https://accounts.zoho.in/oauth/v2/token",
        data={
            "grant_type": "authorization_code",
            "client_id": settings.ZOHO_CLIENT_ID,
            "client_secret": settings.ZOHO_CLIENT_SECRET,
            "code": code,
        },
        timeout=15,
    )
    data = response.json()

    if "refresh_token" not in data:
        print("Exchange failed — Zoho's response:")
        print(data)
        print(
            "\nCommon causes: the code already expired (they're valid ~3-10 min "
            "and single-use — generate a fresh one and re-run immediately), or "
            "it was already used once before."
        )
        sys.exit(1)

    _write_refresh_token(data["refresh_token"])
    print("Success — .env's ZOHO_REFRESH_TOKEN has been updated with the new token.")
    print("Restart the backend now for it to take effect.")


if __name__ == "__main__":
    main()
