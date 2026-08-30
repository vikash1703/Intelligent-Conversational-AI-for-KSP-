import logging

from core.catalyst_client import execute_zcql, zcql_escape
from core.config import settings

logger = logging.getLogger("UserService")

_BASE_COLUMNS = "Username, PasswordHash, Role, EmployeeID, Active"

# Whether AppUser.HomeDistrict/HomeStationID exist on the live table — None
# means "not yet checked this process." A single combined ZCQL SELECT fails
# outright on ANY unknown column, so probing for them inline used to cost a
# full failed-query round trip on every single login (live-measured ~800ms of
# a ~2.1s login, most of it a doomed HomeDistrict attempt repeating forever
# while the column didn't exist). Cached at module scope for the process's
# lifetime instead, same convention as timeline_service.py's
# _case_status_cache — this is a one-time schema-provisioning fact, not
# something that changes per request. A server restart re-probes it, so
# creating a column live in Catalyst still gets picked up on the next
# deploy/restart. Both columns were added together (2026-08-23) so they're
# probed together — if that ever changes, split this into two flags.
_extra_columns_available: bool | None = None
_EXTRA_COLUMNS = "HomeDistrict, HomeStationID"


def get_user_by_username(username: str) -> dict | None:
    """Look up an app user in the AppUser governance table (see AppUser DDL in README).

    Tries HomeDistrict + HomeStationID first (added 2026-07-23 / 2026-08-23
    for role-based data scoping — see
    services/permission_service.get_scoped_station_ids) and falls back to the
    original column set if those columns don't exist yet in Catalyst. In the
    fallback case the returned dict simply has no "HomeDistrict"/"HomeStationID"
    key at all — callers already use `.get()` for both, so this degrades to an
    unscoped user, not a crash."""
    global _extra_columns_available
    safe_username = zcql_escape(username)
    base_cols = ", ".join(f"{settings.APP_USER_TABLE}.{c}" for c in _BASE_COLUMNS.split(", "))

    if _extra_columns_available is not False:
        cols = ", ".join(f"{settings.APP_USER_TABLE}.{c}" for c in (_BASE_COLUMNS + ", " + _EXTRA_COLUMNS).split(", "))
        query = f"SELECT {cols} FROM {settings.APP_USER_TABLE} WHERE {settings.APP_USER_TABLE}.Username = '{safe_username}'"
        try:
            rows = execute_zcql(query)
            _extra_columns_available = True
        except Exception as e:
            logger.info(f"AppUser.HomeDistrict/HomeStationID not queryable yet ({e}) — falling back to base columns, unscoped login")
            _extra_columns_available = False
            query = f"SELECT {base_cols} FROM {settings.APP_USER_TABLE} WHERE {settings.APP_USER_TABLE}.Username = '{safe_username}'"
            rows = execute_zcql(query)
    else:
        query = f"SELECT {base_cols} FROM {settings.APP_USER_TABLE} WHERE {settings.APP_USER_TABLE}.Username = '{safe_username}'"
        rows = execute_zcql(query)

    if not rows:
        return None
    return rows[0].get(settings.APP_USER_TABLE)
