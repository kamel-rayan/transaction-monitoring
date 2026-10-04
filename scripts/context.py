"""
Assemble the full context for one alert: the work an analyst does by hand
across 4-5 screens before deciding anything.

Only uses data available AT ALERT TIME (no peeking at later transactions)
and never exposes ground_truth.

Usage:  python -m scripts.context ALT-2026-00001
"""
import json
import sys
from datetime import datetime

from scripts.db import get_conn


def _d(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def get_alert_context(alert_id: str, conn=None) -> dict:
    own = conn is None
    conn = conn or get_conn()
    try:
        a = conn.execute("""SELECT alert_id, transaction_id, source_system, alert_reason,
                                   alert_severity, created_at, status
                            FROM alerts WHERE alert_id = ?""", (alert_id,)).fetchone()
        if not a:
            raise KeyError(f"Unknown alert {alert_id}")
        t = conn.execute("SELECT * FROM transactions WHERE transaction_id = ?", (a["transaction_id"],)).fetchone()
        cust = conn.execute("SELECT * FROM customers WHERE customer_id = ?", (t["customer_id"],)).fetchone()
        cp = conn.execute("SELECT * FROM counterparties WHERE counterparty_id = ?", (t["counterparty_id"],)).fetchone()
        at = t["timestamp"]

        prior = conn.execute("""SELECT t.*, c.name AS cp_name, c.country AS cp_country
                                FROM transactions t JOIN counterparties c USING(counterparty_id)
                                WHERE t.customer_id = ? AND t.timestamp < ?
                                ORDER BY t.timestamp DESC""", (cust["customer_id"], at)).fetchall()

        with_cp = [p for p in prior if p["counterparty_id"] == cp["counterparty_id"]]
        amounts = [p["amount_sar"] for p in prior]
        same_dir = [p["amount_sar"] for p in prior if p["direction"] == t["direction"]]
        last_24h = [p for p in prior if (_d(at) - _d(p["timestamp"])).total_seconds() <= 86400]
        days_idle = (_d(at) - _d(prior[0]["timestamp"])).days if prior else None
        avg_same_dir = sum(same_dir) / len(same_dir) if same_dir else None

        return {
            "alert": dict(a),
            "flagged_transaction": {
                "transaction_id": t["transaction_id"], "amount_sar": t["amount_sar"],
                "direction": t["direction"], "channel": t["channel"],
                "timestamp": at, "description": t["description"],
            },
            "customer": {
                **{k: cust[k] for k in ("customer_id", "full_name", "customer_type", "nationality", "city",
                                        "occupation_or_sector", "kyc_risk_rating", "avg_monthly_volume_sar")},
                "is_pep": bool(cust["is_pep"]),
                "tenure_years": round((_d(at).date() - _d(cust["onboarded_date"]).date()).days / 365, 1),
            },
            "counterparty": {
                **{k: cp[k] for k in ("counterparty_id", "name", "counterparty_type", "country", "risk_rating")},
                "is_registered_business": bool(cp["is_registered_business"]),
                "on_watchlist": bool(cp["on_watchlist"]),
            },
            "relationship": {
                "prior_transactions_with_counterparty": len(with_cp),
                "first_seen": with_cp[-1]["timestamp"] if with_cp else None,
                "total_prior_amount_sar": round(sum(p["amount_sar"] for p in with_cp), 2),
            },
            "customer_behaviour_90d": {
                "transaction_count": len(prior),
                f"avg_{t['direction']}_amount_sar": round(avg_same_dir, 2) if avg_same_dir else None,
                "max_amount_sar": max(amounts) if amounts else None,
                "flagged_vs_avg_multiple": round(t["amount_sar"] / avg_same_dir, 1) if avg_same_dir else None,
                "transactions_last_24h": len(last_24h),
                "amount_last_24h_sar": round(sum(p["amount_sar"] for p in last_24h), 2),
                "days_since_previous_activity": days_idle,
            },
            "recent_transactions": [
                {"timestamp": p["timestamp"], "amount_sar": p["amount_sar"], "direction": p["direction"],
                 "counterparty": p["cp_name"], "country": p["cp_country"], "description": p["description"]}
                for p in prior[:10]
            ],
        }
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    print(json.dumps(get_alert_context(sys.argv[1] if len(sys.argv) > 1 else "ALT-2026-00001"),
                     indent=2, ensure_ascii=False))
