import logging

from core.catalyst_client import execute_zcql, fetch_all_rows
from core.ttl_cache import ttl_cached

logger = logging.getLogger("FinancialService")

# case_master_id, accused_id, and district_id on FinancialTransaction are all the
# exact same placeholder value on every one of the 2000 live rows (live-verified:
# not sparse, not NULL — a literal constant repeated across the whole table), so this
# table cannot be joined to Case/Accused/District at all. Everything here works off
# the transaction's own fields only: amount, transaction_type, is_suspicious, time.
#
# This ALSO means Financial endpoints cannot be jurisdiction-scoped — there is no
# case/station/district column on this table to filter by, real or derivable
# (confirmed 2026-08-23, Tier 0 audit). Deliberately unscoped, not an oversight;
# the UI surfaces this honestly rather than silently.
_TOP_SUSPICIOUS_LIMIT = 20

# is_suspicious is a RAW, pre-set column in the source data (see
# list_suspicious_transactions below) — there is no scoring formula anywhere in
# this codebase that computes it, live-confirmed 2026-08-23. What real signal
# DOES exist: is_suspicious correlates with transaction_type at very different
# rates per type (Cash 93.9%, Crypto 68.7%, Hawala Net 66.9%, Wire Transfer
# 61.4% — real, live-computed, see get_suspicious_type_context below), and a
# transaction's amount can be compared to its own type's real distribution. This
# is honestly labeled CONTEXT, not a "reason" — the true rule (if any) behind
# is_suspicious isn't visible from this codebase; showing a real, computed
# statistical fact next to an unexplained flag is different from claiming to
# have reverse-engineered why any specific row was flagged, and the UI must not
# blur that line.
_HIGH_TAIL_PERCENTILE = 0.9


def get_transaction_summary() -> dict:
    """Volume and value breakdown by transaction_type and by suspicious flag, pushed
    down as native ZCQL GROUP BY + SUM/AVG — no pagination needed, the grouped result
    set is tiny regardless of table size."""
    by_type_rows = execute_zcql(
        "SELECT FinancialTransaction.transaction_type, COUNT(FinancialTransaction.ROWID), "
        "SUM(FinancialTransaction.amount) FROM FinancialTransaction "
        "GROUP BY FinancialTransaction.transaction_type"
    )
    by_type = [
        {
            "transaction_type": r.get("FinancialTransaction", r).get("transaction_type"),
            "count": int(r.get("FinancialTransaction", r).get("COUNT(ROWID)", 0)),
            "total_amount": int(float(r.get("FinancialTransaction", r).get("SUM(amount)", 0))),
        }
        for r in by_type_rows
    ]

    suspicious_rows = execute_zcql(
        "SELECT FinancialTransaction.is_suspicious, COUNT(FinancialTransaction.ROWID), "
        "SUM(FinancialTransaction.amount) FROM FinancialTransaction "
        "GROUP BY FinancialTransaction.is_suspicious"
    )
    by_suspicious = [
        {
            "is_suspicious": r.get("FinancialTransaction", r).get("is_suspicious") == "1",
            "count": int(r.get("FinancialTransaction", r).get("COUNT(ROWID)", 0)),
            "total_amount": int(float(r.get("FinancialTransaction", r).get("SUM(amount)", 0))),
        }
        for r in suspicious_rows
    ]

    avg_rows = execute_zcql("SELECT AVG(FinancialTransaction.amount) FROM FinancialTransaction")
    average_amount = round(float(avg_rows[0].get("FinancialTransaction", avg_rows[0]).get("AVG(amount)", 0)), 2) if avg_rows else 0.0

    return {
        "by_transaction_type": sorted(by_type, key=lambda x: x["total_amount"], reverse=True),
        "by_suspicious_flag": by_suspicious,
        "average_amount": average_amount,
    }


@ttl_cached()
def _get_all_transactions() -> list[dict]:
    """Every real FinancialTransaction row (2,000 total, well within a handful
    of ZCQL's 300-row pages) — fully paginated. TTL-cached since this table
    doesn't change mid-demo/session (same convention as
    db_service.get_all_accused_rows, added the same day for the identical
    reason: this table is small enough to hold in memory once rather than
    re-querying per request).

    REAL BUG FIXED 2026-08-23, found while building the Financial
    Intelligence UI: list_suspicious_transactions used to push its own LIMIT
    into the WHERE clause BEFORE sorting by amount — meaning "top N suspicious
    transactions by amount" was actually whichever N happened to come back
    first in ZCQL's own (unspecified, not amount-ordered) row order, then
    sorted only among themselves. A real top-N by amount needs every matching
    row fetched first, sorted, then paginated — not the other way around.

    UPGRADED same day (codebase-wide pagination audit) to cursor-based
    pagination (core.catalyst_client.fetch_all_rows) — offset pagination can
    both duplicate AND silently drop a real row, which a from-scratch
    transaction_id dedup here would NOT have caught (see fetch_all_rows'
    docstring for the direct proof on CaseMaster)."""
    return fetch_all_rows(
        "FinancialTransaction",
        ["transaction_id", "amount", "transaction_type", "is_suspicious", "CREATEDTIME"],
    )


@ttl_cached()
def get_suspicious_type_context() -> dict:
    """Real, live-computed context per transaction_type: how often this type
    is flagged suspicious, and the amount that marks its own top 10% (see
    _HIGH_TAIL_PERCENTILE) — used to give each suspicious row honest
    statistical context, not a fabricated "reason" (see module docstring)."""
    by_type: dict[str, list] = {}
    for row in _get_all_transactions():
        by_type.setdefault(row["transaction_type"], []).append(row)

    context = {}
    for t, rows in by_type.items():
        suspicious_count = sum(1 for r in rows if r.get("is_suspicious") == "1")
        amounts = sorted(int(float(r["amount"])) for r in rows)
        high_tail_amount = amounts[int(len(amounts) * _HIGH_TAIL_PERCENTILE)] if amounts else None
        context[t] = {
            "total_count": len(rows),
            "suspicious_count": suspicious_count,
            "suspicious_rate_pct": round(suspicious_count / len(rows) * 100, 1) if rows else 0.0,
            "high_tail_amount": high_tail_amount,
        }
    return context


@ttl_cached()
def get_monthly_transaction_summary() -> list[dict]:
    """Real transaction volume by month (from CREATEDTIME) x suspicious flag
    — added 2026-08-27 for the Financial Intelligence page's timeline chart,
    a pure additive read over the same already-cached _get_all_transactions()
    every other function here already uses; no existing function's logic
    touched. CREATEDTIME carries a real timestamp on all 2,000 rows (this is
    when the seed row was inserted, not a real transaction date — surfaced
    honestly in the UI's own caption, same as every other timestamp caveat
    in this module)."""
    by_month: dict[str, dict[str, int]] = {}
    for row in _get_all_transactions():
        created = row.get("CREATEDTIME")
        if not created:
            continue
        month = str(created)[:7]  # "YYYY-MM"
        bucket = by_month.setdefault(month, {"suspicious": 0, "normal": 0})
        bucket["suspicious" if row.get("is_suspicious") == "1" else "normal"] += 1
    return [
        {"month": m, "suspicious": v["suspicious"], "normal": v["normal"]}
        for m, v in sorted(by_month.items())
    ]


def list_suspicious_transactions(
    limit: int = _TOP_SUSPICIOUS_LIMIT, offset: int = 0, transaction_type: str | None = None,
    amount_min: float | None = None, amount_max: float | None = None,
) -> dict:
    """Transactions flagged is_suspicious=1, genuinely ranked by amount
    (highest first — see _get_all_transactions' docstring for the ordering
    bug this replaces), real server-side pagination. A flat list, not a
    graph: there's no counterparty/sender-receiver column on this table (see
    module docstring), so transactions can't be linked to each other or to a
    case/accused. Each row carries real statistical context (its type's
    real suspicious-rate, and whether its own amount sits in that type's top
    10%) — labeled as context in the API and UI, never as "the reason".

    transaction_type added 2026-08-26 so the UI's type-breakdown table can
    filter this same list by type (exact match against the real
    transaction_type value) — applied before pagination, same as every other
    filter here, so `total` always reflects the filtered count.

    amount_min/amount_max added 2026-08-27 for the UI's amount-range filter
    chips — same "filter the full real list before pagination" contract, so
    an amount-range filter works across all matching rows, not just
    whichever page happened to already be loaded client-side."""
    type_context = get_suspicious_type_context()
    suspicious = [r for r in _get_all_transactions() if r.get("is_suspicious") == "1"]
    if transaction_type:
        suspicious = [r for r in suspicious if r.get("transaction_type") == transaction_type]
    if amount_min is not None:
        suspicious = [r for r in suspicious if float(r["amount"]) >= amount_min]
    if amount_max is not None:
        suspicious = [r for r in suspicious if float(r["amount"]) <= amount_max]
    suspicious.sort(key=lambda r: float(r["amount"]), reverse=True)

    total = len(suspicious)
    page = suspicious[offset:offset + limit]
    transactions = []
    for r in page:
        amount = int(float(r["amount"]))
        t = r["transaction_type"]
        ctx = type_context.get(t, {})
        transactions.append({
            "transaction_id": r.get("transaction_id"),
            "amount": amount,
            "transaction_type": t,
            "recorded_at": r.get("CREATEDTIME"),
            "type_suspicious_rate_pct": ctx.get("suspicious_rate_pct"),
            "is_high_tail_for_type": ctx.get("high_tail_amount") is not None and amount >= ctx["high_tail_amount"],
        })
    return {"total": total, "transactions": transactions}
