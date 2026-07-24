"""One-shot smoke test for get_case_full — run standalone to avoid tripping
Zoho's rate limiter with many separate short-lived processes."""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.catalyst_client import execute_zcql
from services.db_service import get_case_full

print("--- Direct quoted-literal child query (known-linked case_id) ---")
rows = execute_zcql(
    "SELECT Accused.AccusedMasterID, Accused.AccusedName FROM Accused "
    "WHERE Accused.CaseMasterID = '43437000000082165'"
)
print(rows)

print()
print("--- get_case_full for a case with NO linked children (expect clean empty lists, no ZCQL errors) ---")
case = get_case_full("100091036201900002")
print(json.dumps(case, indent=2, default=str))

print()
print("--- get_case_full for a case that IS linked (CaseMasterID 43437000000082165) ---")
rows2 = execute_zcql("SELECT CaseMaster.ROWID, CaseMaster.CrimeNo FROM CaseMaster WHERE CaseMaster.ROWID = '43437000000082165'")
print(rows2)
