import logging

from core.catalyst_client import execute_zcql, zcql_escape
from core.config import settings

logger = logging.getLogger("UserService")

_BASE_COLUMNS = "Username, PasswordHash, Role, EmployeeID, Active"


def get_user_by_username(username: str) -> dict | None:
    """Look up an app user in the AppUser governance table (see AppUser DDL in README).

    Tries HomeDistrict first (added 2026-07-23 for role-based data scoping —
    see services/permission_service.get_district_scope) and falls back to the
    original column set if that column doesn't exist yet in Catalyst — a
    single combined ZCQL SELECT fails outright on ANY unknown column, so
    without this fallback, login would break for every user until the
    console-side column is created. In the fallback case the returned dict
    simply has no "HomeDistrict" key at all — callers already use `.get()`
    for it, so this degrades to an unscoped user, not a crash."""
    safe_username = zcql_escape(username)
    cols = ", ".join(f"{settings.APP_USER_TABLE}.{c}" for c in (_BASE_COLUMNS + ", HomeDistrict").split(", "))
    query = f"SELECT {cols} FROM {settings.APP_USER_TABLE} WHERE {settings.APP_USER_TABLE}.Username = '{safe_username}'"
    try:
        rows = execute_zcql(query)
    except Exception as e:
        logger.info(f"AppUser.HomeDistrict not queryable yet ({e}) — falling back to base columns, unscoped login")
        base_cols = ", ".join(f"{settings.APP_USER_TABLE}.{c}" for c in _BASE_COLUMNS.split(", "))
        query = f"SELECT {base_cols} FROM {settings.APP_USER_TABLE} WHERE {settings.APP_USER_TABLE}.Username = '{safe_username}'"
        rows = execute_zcql(query)

    if not rows:
        return None
    return rows[0].get(settings.APP_USER_TABLE)
