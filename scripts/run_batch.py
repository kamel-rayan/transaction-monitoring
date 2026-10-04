"""
Triage many alerts and score the results against ground truth.

Usage:  python -m scripts.run_batch                 # all 1,000 alerts
        python -m scripts.run_batch --limit 50      # first 50 (use this to test Claude cheaply)
        python -m scripts.run_batch --mode rules
"""
import argparse
from collections import Counter

from scripts import config
from scripts.db import get_conn
from scripts.triage_engine import triage_alert, triage_mode


def reset_decisions(conn):
    """Clear previous triage results (the audit log keeps the full history)."""
    conn.execute("DELETE FROM triage_decisions")
    conn.execute("UPDATE alerts SET status='new'")
    conn.execute("INSERT INTO audit_log(actor,event_type,details) VALUES ('system','batch_reset','{}')")
    conn.commit()


def evaluate(conn):
    rows = conn.execute("""SELECT a.ground_truth AS truth, d.decision, d.assessment
                           FROM alerts a JOIN triage_decisions d USING(alert_id)""").fetchall()
    n = len(rows)
    if not n:
        print("No decisions yet.")
        return
    dec = Counter(r["decision"] for r in rows)
    gen = [r for r in rows if r["truth"] == "genuine_risk"]
    fps = [r for r in rows if r["truth"] == "false_positive"]
    missed = [r for r in gen if r["decision"] == "auto_dismiss"]
    fp_cleared = [r for r in fps if r["decision"] == "auto_dismiss"]
    to_human = dec["flag_for_review"] + dec["escalate_senior"]

    before_h = n * config.MANUAL_REVIEW_MINUTES / 60
    after_h = to_human * config.ASSISTED_REVIEW_MINUTES / 60

    print(f"\n=== TRIAGE RESULTS ({n} alerts) ===")
    print(f"Auto-dismissed:          {dec['auto_dismiss']:>5}  ({dec['auto_dismiss'] / n:.0%})")
    print(f"Flagged for review:      {dec['flag_for_review']:>5}  ({dec['flag_for_review'] / n:.0%})")
    print(f"Escalated to senior:     {dec['escalate_senior']:>5}  ({dec['escalate_senior'] / n:.0%})")
    print("\n--- Accuracy vs ground truth ---")
    print(f"Genuine risks caught:    {len(gen) - len(missed)}/{len(gen)}"
          f"  ({(len(gen) - len(missed)) / max(len(gen), 1):.1%})   <- must be ~100%")
    print(f"Genuine risks MISSED:    {len(missed)}   (auto-dismissed by mistake)")
    print(f"False positives cleared: {len(fp_cleared)}/{len(fps)}  ({len(fp_cleared) / max(len(fps), 1):.0%})")
    print("\n--- Analyst workload ---")
    print(f"Before: {n} alerts x {config.MANUAL_REVIEW_MINUTES:.0f} min = {before_h:,.0f} hours")
    print(f"After:  {to_human} alerts x {config.ASSISTED_REVIEW_MINUTES:.0f} min = {after_h:,.0f} hours")
    print(f"Time saved: {1 - after_h / before_h:.0%}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--mode", choices=["rules", "claude"])
    ap.add_argument("--reset-only", action="store_true", help="clear decisions, put all alerts back to 'new'")
    a = ap.parse_args()
    mode = a.mode or triage_mode()

    conn = get_conn()
    reset_decisions(conn)
    if a.reset_only:
        print("All alerts reset to 'new'. Ready for n8n.")
        return
    ids = [r[0] for r in conn.execute("SELECT alert_id FROM alerts ORDER BY alert_id"
                                      + (f" LIMIT {int(a.limit)}" if a.limit else ""))]
    conn.close()

    print(f"Triaging {len(ids)} alerts in '{mode}' mode"
          + (f" with {config.CLAUDE_MODEL}" if mode == "claude" else " (practice mode, no API calls)"))
    for i, aid in enumerate(ids, 1):
        triage_alert(aid, mode)
        if i % 100 == 0 or i == len(ids):
            print(f"  {i}/{len(ids)} done")

    conn = get_conn()
    evaluate(conn)
    conn.close()


if __name__ == "__main__":
    main()
