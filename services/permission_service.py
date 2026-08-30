import logging

from core.catalyst_client import execute_zcql
from core.exceptions import AppException

logger = logging.getLogger("PermissionService")

# Admin is a system-administration bypass role, not a real police rank, so it has
# no row in RolePermission — always resolves to full access without a DB call.
_ADMIN_PERMISSIONS = {
    "district_access": "ALL",
    "data_level": "Full",
    "can_view_network": True,
    "can_export": True,
    "can_register_fir": True,
}

# RolePermission is small, near-static reference data (one row per police rank) —
# cached in-process the same way services/auth_service.py caches the Zoho access
# token, rather than re-querying Catalyst on every permission check.
_cache: dict[str, dict] = {}
_cache_loaded = False

# can_register_fir (added 2026-08-28, FIR Registration module) is queried
# SEPARATELY from the main cache load above, deliberately — the column
# can't be added via the API (OAuth scope gap, live-verified) and depends on
# the user adding it via the Catalyst console. Folding it into the main
# SELECT would mean a "Unknown Column" ZCQL error on THAT query breaks
# can_view_network/can_export for every role app-wide the moment this
# module ships, not just gates the one new feature — an isolated,
# defensively-caught query keeps the blast radius to just can_register_fir
# itself (defaults to False everywhere) until the column actually exists.
_fir_cache: dict[str, bool] = {}
_fir_cache_loaded = False


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


def _load_fir_cache() -> None:
    global _fir_cache_loaded
    try:
        rows = execute_zcql("SELECT RolePermission.role_name, RolePermission.can_register_fir FROM RolePermission")
    except Exception as e:
        logger.warning(f"can_register_fir column not queryable yet (needs console add) — denying by default: {e}")
        _fir_cache_loaded = True
        return
    for r in rows:
        row = r.get("RolePermission", r)
        _fir_cache[row["role_name"]] = bool(row.get("can_register_fir"))
    _fir_cache_loaded = True


def get_permissions(role_name: str) -> dict:
    """Permission flags for a role, matching the live RolePermission schema
    (district_access/data_level/can_view_network/can_export/can_register_fir)."""
    if role_name == "Admin":
        return _ADMIN_PERMISSIONS

    if not _cache_loaded:
        _load_cache()
    if not _fir_cache_loaded:
        _load_fir_cache()

    permissions = _cache.get(role_name)
    if permissions is None:
        logger.error(f"No RolePermission row found for role '{role_name}' — denying by default")
        return {"district_access": None, "data_level": None, "can_view_network": False, "can_export": False, "can_register_fir": False}
    return {**permissions, "can_register_fir": _fir_cache.get(role_name, False)}


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

    Returns None (unscoped) only if the role's own access level is ALL.

    FAILS CLOSED (raises AppException, 403) if this specific user has no
    home_district set. Changed 2026-08-23 from the original fail-OPEN
    behavior (silently returning None / unscoped access) after live-
    verifying that fail-open behavior was actively granting every non-ALL-
    role user full statewide access: AppUser.HomeDistrict does not exist as
    a live Catalyst column at all (confirmed via a direct ZCQL probe — "Unkown
    Column HomeDistrict"), so current_user.home_district is None for every
    single user regardless of role, and INSPECTORTEST (Station-level, the
    most restrictive real role) was live-verified receiving results
    spanning 34 distinct police stations from one unfiltered /cases/search
    call. "Blocking access entirely over missing profile data" is a worse
    UX than unscoped access but is the only defensible default for a
    law-enforcement access-control gate — an officer whose jurisdiction
    isn't configured must get an explicit, loud denial, never the full
    dataset by default. This intentionally makes every non-ALL-role login
    (IGP/SP/Inspector) unable to reach any case-scoped endpoint until
    AppUser.HomeDistrict is actually provisioned with real data — see the
    Tier 0 report for why that provisioning is currently blocked (both an
    OAuth scope gap for creating the column, and a data gap: neither
    candidate method for deriving Unit.DistrictID from real data actually
    resolves — live-tested, 0/40 stations show a confident single-district
    majority from case-coordinate centroids, and station names are drawn
    from only 7 distinct templates repeated across 40 rows with no
    per-row geographic specificity)."""
    permissions = get_permissions(current_user.role.value)
    access_level = permissions.get("district_access")
    if access_level in (None, "ALL"):
        return None
    if not current_user.home_district:
        logger.warning(
            f"{current_user.username} ({current_user.role.value}, district_access={access_level}) has no "
            "HomeDistrict set — denying access (fail closed)"
        )
        raise AppException(
            "Your jurisdiction is not configured. Contact an administrator to set your home "
            "district before you can access case data.",
            status_code=403,
        )
    return current_user.home_district


def describe_scope(role_value: str, home_district: str | None, home_station_name: str | None) -> str:
    """Human-readable jurisdiction label for the UI header (added 2026-08-23,
    item 2 of the Tier 0 extension — "make the active scope visible in the
    UI header... so it is demonstrable"). Computed once, backend-side, and
    embedded directly in the JWT (see core/security.create_access_token) —
    the frontend just displays this string verbatim, no permission logic
    duplicated client-side, same "one shared resolution function" principle
    as get_scoped_station_ids itself.

    Mirrors get_district_scope's own real behavior exactly, including its
    fail-closed case — a role with no home_district configured will 403 the
    moment it hits any actual case-scoped endpoint, so the header says so
    plainly up front rather than showing a misleadingly generic label."""
    permissions = get_permissions(role_value)
    access_level = permissions.get("district_access")
    if access_level in (None, "ALL"):
        return "State — All Districts"
    if not home_district:
        return "Jurisdiction not configured"
    if access_level == "Station" and home_station_name:
        return f"{home_district} — {home_station_name}"
    return home_district


def get_scoped_station_ids(current_user) -> list[int] | None:
    """The single, shared entry point every case-touching endpoint should use
    for jurisdiction scoping (added 2026-08-23, extending the district-only
    enforcement from Tier 0 — see services/db_service.get_station_ids_for_district
    and this module's get_district_scope, which this wraps rather than
    replaces). Returns None for unrestricted access (ALL-access roles), else a
    list of CaseMaster.PoliceStationID values a caller should filter to —
    never an empty list AND None interchangeably: None means "don't filter
    at all", [] means "this scope currently matches zero stations" (a real,
    correctly-scoped-but-thin state, e.g. a district with no station->district
    links populated yet — see the Tier 0 report).

    Station-level roles (Inspector) now get TRUE single-station scoping when
    current_user.home_station_id is set (AppUser.HomeStationID, added
    2026-08-23) — the whole reason this wrapper exists instead of every
    caller just calling get_district_scope() + get_station_ids_for_district()
    directly, which could only ever approximate Station-level access as
    District-level (see get_district_scope's own docstring on why that
    approximation existed and was the safe direction to err in). Falls back
    to that same district-wide approximation when home_station_id isn't set
    (an older token issued before this column existed, or a user whose
    station specifically — as opposed to district — was never configured),
    for any role, not just Inspector — same non-fatal degradation pattern as
    every other optional scoping field in this codebase.

    A district-level role (SP) or the Zone-approximated-as-District role
    (IGP) always gets the district-wide list, regardless of whether
    home_station_id happens to be set — home_station_id only narrows further
    for a role whose OWN access level is already Station, never widens or
    narrows a broader role's legitimate district-wide entitlement."""
    from services.db_service import get_station_ids_for_district

    district = get_district_scope(current_user)
    if district is None:
        return None

    permissions = get_permissions(current_user.role.value)
    if permissions.get("district_access") == "Station" and current_user.home_station_id:
        try:
            return [int(current_user.home_station_id)]
        except (TypeError, ValueError):
            logger.warning(
                f"{current_user.username} has a non-numeric HomeStationID "
                f"({current_user.home_station_id!r}) — falling back to district-wide scoping"
            )
    return get_station_ids_for_district(district)
