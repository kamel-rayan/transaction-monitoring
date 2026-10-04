"""Run: python -m pytest -q   (after python -m scripts.generate_data)"""
import json

from scripts.context import get_alert_context
from scripts.db import get_conn


def q(sql):
    with get_conn() as c:
        return c.execute(sql).fetchone()[0]


def test_volumes():
    assert q("SELECT COUNT(*) FROM alerts") == 1000
    assert q("SELECT COUNT(*) FROM transactions") > 10000


def test_false_positive_ratio_realistic():
    fp = q("SELECT AVG(ground_truth='false_positive') FROM alerts")
    assert 0.65 <= fp <= 0.75


def test_no_orphans():
    assert q("""SELECT COUNT(*) FROM alerts a LEFT JOIN transactions t USING(transaction_id)
                WHERE t.transaction_id IS NULL""") == 0
    assert q("""SELECT COUNT(*) FROM transactions t LEFT JOIN counterparties c USING(counterparty_id)
                WHERE c.counterparty_id IS NULL""") == 0


def test_context_never_leaks_ground_truth():
    for aid in ["ALT-2026-00001", "ALT-2026-00500", "ALT-2026-01000"]:
        blob = json.dumps(get_alert_context(aid))
        assert "ground_truth" not in blob
        assert "false_positive" not in blob and "genuine_risk" not in blob


def test_context_uses_only_past_data():
    ctx = get_alert_context("ALT-2026-00042")
    flagged = ctx["flagged_transaction"]["timestamp"]
    assert all(t["timestamp"] < flagged for t in ctx["recent_transactions"])
