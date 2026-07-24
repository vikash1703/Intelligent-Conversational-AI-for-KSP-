import logging

from core.catalyst_client import execute_zcql

logger = logging.getLogger("FinancialService")

# case_master_id, accused_id, and district_id on FinancialTransaction are all the
# exact same placeholder value on every one of the 2000 live rows (live-verified:
# not sparse, not NULL — a literal constant repeated across the whole table), so this
# table cannot be joined to Case/Accused/District at all. Everything here works off
# the transaction's own fields only: amount, transaction_type, is_suspicious, time.
_TOP_SUSPICIOUS_LIMIT = 20


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


def list_suspicious_transactions(limit: int = _TOP_SUSPICIOUS_LIMIT) -> list:
    """Highest-value transactions flagged is_suspicious=1 — a flat list, not a graph:
    there's no counterparty/sender-receiver column on this table (see module
    docstring), so transactions can't be linked to each other or to a case/accused."""
    rows = execute_zcql(
        "SELECT FinancialTransaction.transaction_id, FinancialTransaction.amount, "
        "FinancialTransaction.transaction_type, FinancialTransaction.CREATEDTIME "
        "FROM FinancialTransaction WHERE FinancialTransaction.is_suspicious = 1 "
        f"LIMIT {limit}"
    )
    transactions = [
        {
            "transaction_id": r.get("FinancialTransaction", r).get("transaction_id"),
            "amount": int(float(r.get("FinancialTransaction", r).get("amount", 0))),
            "transaction_type": r.get("FinancialTransaction", r).get("transaction_type"),
            "recorded_at": r.get("FinancialTransaction", r).get("CREATEDTIME"),
        }
        for r in rows
    ]
    return sorted(transactions, key=lambda t: t["amount"], reverse=True)
