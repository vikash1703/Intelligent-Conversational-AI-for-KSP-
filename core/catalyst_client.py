import logging
import re
import requests
from core.config import settings
from core.exceptions import AppException
from services.auth_service import TokenManager

logger = logging.getLogger("CatalystClient")

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_date(value: str, field_name: str = "date") -> str:
    """Validate a caller-supplied date string is YYYY-MM-DD before it's ever
    interpolated into a ZCQL query. Live-verified a malformed value (e.g. plain
    garbage text) reliably makes ZCQL itself reject the query — surfacing through
    execute_zcql as a misleading 502 "upstream failure" for what is really bad
    client input, same class of issue as the crime_no/accused_id format checks
    elsewhere in this codebase. Raises ValueError so callers can convert it to a
    400 the same way."""
    if not _DATE_PATTERN.match(value or ""):
        raise ValueError(f"Invalid {field_name} format, expected YYYY-MM-DD")
    return value

CATALYST_BASE_URL = "https://api.catalyst.zoho.in/baas/v1"


class CatalystQueryError(AppException):
    """Raised when a ZCQL query or Data Store write fails — deliberately distinct
    from a genuine zero-row/empty result. Both execute_zcql and insert_row used to
    swallow every failure (bad query, timeout, network error, rate limit) into a
    silent [] or {}, making a real backend failure indistinguishable from "no such
    record" to every caller — this was already the root cause of one past incident
    (a ZCQL LIMIT>300 query looked like "no data" instead of a query error). Subclasses
    AppException so it gets the same {"error": true, "message", "path"} response shape
    for free via the existing handler, with a 502 (upstream/dependency failure, not
    the caller's fault) instead of AppException's 500 default."""
    def __init__(self, message: str):
        super().__init__(message, status_code=502)


def _headers() -> dict:
    return {
        "Authorization": f"Zoho-oauthtoken {TokenManager.get_token()}",
        "CATALYST-ORG": settings.ZOHO_ORG_ID,
        "Environment": settings.CATALYST_ENVIRONMENT,
        "Content-Type": "application/json",
    }


def zcql_escape(value: str) -> str:
    """Escape a value for safe interpolation into a ZCQL string literal.

    Doubled-quote escaping (SQL-92 style: `'` -> `''`), not backslash-
    escaping — live-verified 2026-07-23 that ZCQL does NOT unescape a
    backslash-quote sequence on the read side, even though the identical
    value round-trips fine through the JSON insert API. A name like "Umesh
    D'Souza" would insert correctly, but `WHERE AccusedName = 'Umesh D\'Souza'`
    silently matched zero rows (no error — the dangerous kind of wrong).
    Switched to `''`, confirmed via direct A/B query against the live
    Accused table: 0 rows with the old escaping, 1 correct row with this one.
    Also re-verified this doesn't reopen an injection vector — classic
    payloads (`' OR '1'='1`, `'; DROP TABLE Accused; --`) still come back as
    inert 0-row literals under the new escaping, not executable/breaking
    syntax."""
    return str(value).replace("'", "''")


def execute_zcql(query: str) -> list:
    """Run a read-only ZCQL SELECT query and return the row list. Raises
    CatalystQueryError on failure — an empty list from this function always means
    "query succeeded, zero matching rows," never "the query failed"."""
    url = f"{CATALYST_BASE_URL}/project/{settings.ZOHO_PROJECT_ID}/query"
    try:
        response = requests.post(url, json={"query": query}, headers=_headers(), timeout=10)
    except requests.exceptions.RequestException as e:
        logger.error(f"ZCQL network error: {str(e)}")
        raise CatalystQueryError("Database query failed: network error") from e

    if response.status_code == 200:
        return response.json().get("data", [])

    logger.error(f"ZCQL error {response.status_code}: {response.text}")
    raise CatalystQueryError(f"Database query failed (status {response.status_code})")


def insert_row(table_name: str, data: dict) -> dict:
    """Insert a single row into a Catalyst Data Store table.

    The Insert Row API expects the request body to be a raw JSON array of row
    objects (e.g. `[{"col": "val"}]`), NOT `{"data": {...}}` or `{"data": [...]}` —
    both of those silently fail with a generic 400 INVALID_INPUT that gives no hint
    which field is wrong. Verified directly against the live API."""
    url = f"{CATALYST_BASE_URL}/project/{settings.ZOHO_PROJECT_ID}/table/{table_name}/row"
    try:
        response = requests.post(url, json=[data], headers=_headers(), timeout=10)
    except requests.exceptions.RequestException as e:
        logger.error(f"Insert network error on {table_name}: {str(e)}")
        raise CatalystQueryError(f"Failed to write to {table_name}: network error") from e

    if response.status_code in (200, 201):
        return response.json()

    logger.error(f"Insert error on {table_name} {response.status_code}: {response.text}")
    raise CatalystQueryError(f"Failed to write to {table_name} (status {response.status_code})")


def update_rows(table_name: str, rows: list[dict]) -> dict:
    """Update one or more existing rows in a Catalyst Data Store table.

    Same array-wrapping convention as insert_row (verified against Catalyst docs,
    not assumed from insert's shape): PUT to the same .../table/{table_name}/row
    URL with a JSON array of row objects, each of which must include its own
    ROWID alongside the columns being changed. Batches multiple rows into one
    HTTP call rather than one call per row — required at the row counts this
    project's bulk data-linking scripts operate on (thousands of rows)."""
    url = f"{CATALYST_BASE_URL}/project/{settings.ZOHO_PROJECT_ID}/table/{table_name}/row"
    try:
        response = requests.put(url, json=rows, headers=_headers(), timeout=30)
    except requests.exceptions.RequestException as e:
        logger.error(f"Update network error on {table_name}: {str(e)}")
        raise CatalystQueryError(f"Failed to update {table_name}: network error") from e

    if response.status_code in (200, 201):
        return response.json()

    logger.error(f"Update error on {table_name} {response.status_code}: {response.text}")
    raise CatalystQueryError(f"Failed to update {table_name} (status {response.status_code})")


def delete_row(table_name: str, row_id) -> None:
    """Delete a single row by ROWID — confirmed endpoint shape (Catalyst docs +
    web search, since the bulk-delete endpoint's exact request-body format
    isn't documented anywhere fetchable): DELETE .../table/{table_name}/row/{row_id},
    no request body. Used in a loop rather than guessing at the undocumented
    bulk-delete payload shape — chat-session deletes are a handful to a few
    dozen rows at most, so one HTTP call per row is a fine trade for certainty
    over speed."""
    url = f"{CATALYST_BASE_URL}/project/{settings.ZOHO_PROJECT_ID}/table/{table_name}/row/{row_id}"
    try:
        response = requests.delete(url, headers=_headers(), timeout=10)
    except requests.exceptions.RequestException as e:
        logger.error(f"Delete network error on {table_name}/{row_id}: {str(e)}")
        raise CatalystQueryError(f"Failed to delete from {table_name}: network error") from e

    if response.status_code in (200, 201, 204):
        return

    logger.error(f"Delete error on {table_name}/{row_id} {response.status_code}: {response.text}")
    raise CatalystQueryError(f"Failed to delete from {table_name} (status {response.status_code})")
