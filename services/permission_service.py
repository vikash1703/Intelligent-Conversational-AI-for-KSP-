import logging

from core.catalyst_client import execute_zcql

logger = logging.getLogger("PermissionService")

# Admin is a system-administration bypass role, not a real police rank, so it has
# no row in RolePermission — always resolves to full access without a DB call.
_ADMIN_PERMISSIONS = {
    "district_access": "ALL",
    "data_level": "Full",
    "can_view_network": True,
    "can_export": True,
}

# RolePermission is small, near-static reference data (one row per police rank) —
# cached in-process the same way services/auth_service.py caches the Zoho access
# token, rather than re-querying Catalyst on every permission check.
_cache: dict[str, dict] = {}
_cache_loaded = False


def _load_cache() -> None:
    global _cache_loaded
    rows = execute_zcql(
        "SELECT RolePermission.role_name, RolePermission.district_access, "
        "RolePermission.data_level, RolePermission.can_view_network, "
        "RolePermission.can_export FROM RolePermission"
    )
    for r in rows:
        row = r.get("RolePermission", r)
        _cache[row["role_name"]] = {
            "district_access": row.get("district_access"),
            "data_level": row.get("data_level"),
            "can_view_network": row.get("can_view_network"),
            "can_export": row.get("can_export"),
        }
    _cache_loaded = True


def get_permissions(role_name: str) -> dict:
    """Permission flags for a role, matching the live RolePermission schema
    (district_access/data_level/can_view_network/can_export)."""
    if role_name == "Admin":
        return _ADMIN_PERMISSIONS

    if not _cache_loaded:
        _load_cache()

    permissions = _cache.get(role_name)
    if permissions is None:
        logger.error(f"No RolePermission row found for role '{role_name}' — denying by default")
        return {"district_access": None, "data_level": None, "can_view_network": False, "can_export": False}
    return permissions


def get_district_scope(current_user) -> str | None:
    """Returns the district NAME (see services/db_service.
    get_station_ids_for_district) to scope this officer's case-data queries
    to, or None for "don't scope — full/unrestricted access". Added
    2026-07-23: RolePermission's district_access column (ALL/Zone/District/
    Station) was live-verified to be stored but never actually enforced
    anywhere in the codebase before this — this is the first real
    enforcement point.

    "Zone"-level access (IGP) has no real implementation here: this schema
    has no Zone concept at all — no Zone table, no zone-to-district grouping
    anywhere (confirmed live 2026-07-23) — so it's treated as District-level
    (the officer's own home district) as the closest honest approximation,
    rather than either fabricating an arbitrary zone grouping or silently
    granting IGP unrestricted full access. Revisit if real zone reference
    data ever gets sourced.

    "Station"-level access (Inspector) similarly resolves to the officer's
    whole home DISTRICT, not just their home station — AppUser only has
    HomeDistrict (added this session), no HomeStation column yet. This is
    the correct, safe direction to approximate in: it can only show a
    Station-level officer MORE than they're nominally entitled to (their
    whole district, not just their station), never less — a real narrowing
    to station level needs a HomeStation column as a follow-up, not a
    silent gap in the meantime.

    Returns None (unscoped) if the role's own access level is ALL, or if
    this specific user has no home_district set (nothing to scope BY yet —
    logged as a warning since it's a real access-control gap for that user,
    not a legitimate "no restriction" case, but blocking their access
    entirely over missing profile data would be a worse failure mode)."""
    permissions = get_permissions(current_user.role.value)
    access_level = permissions.get("district_access")
    if access_level in (None, "ALL"):
        return None
    if not current_user.home_district:
        logger.warning(
            f"{current_user.username} ({current_user.role.value}, district_access={access_level}) has no "
            "HomeDistrict set — cannot scope this user's data, granting unscoped access"
        )
        return None
    return current_user.home_district
