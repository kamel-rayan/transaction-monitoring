"""
Step 5 - Audit trail and analyst feedback.

Every automated decision is already logged by the triage engine. This module adds
the human side: analysts confirming or overturning decisions, and a full,
time-ordered history per alert for auditors.

Usage:
  python -m scripts.audit_logger history ALT-2026-00007
  python -m scripts.audit_logger override ALT-2026-00007 --analyst A.Rashid --verdict confirmed --note "SAR filed"
  python -m scripts.audit_logger simulate-reviews     # DEMO: analysts review every alert routed to humans
"""
import argparse
import json

from scripts.db import get_conn

VERDICTS = ("confirmed", "overturned")


def record_override(alert_id: str, analyst: str, verdict: str, note: str = "", conn=None) -> dict:
    """An analyst reviews the AI decision. 'confirmed' = AI was right; 'overturned' = AI was wrong."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    own = conn is None
    conn = conn or get_conn()
    try:
        d = conn.execute("""SELECT decision_id, decision FROM triage_decisions WHERE alert_id=?
                            ORDER BY decision_id DESC LIMIT 1""", (alert_id,)).fetchone()
        if not d:
            raise KeyError(f"No triage decision for {alert_id}")
        conn.execute("UPDATE triage_decisions SET analyst_override=?, override_by=?, override_note=? WHERE decision_id=?",
                     (verdict, analyst, note, d["decision_id"]))
        # Overturning an auto-dismissal re-opens the alert; overturning a flag closes it.
        if verdict == "overturned":
            status = "flagged_for_review" if d["decision"] == "auto_dismiss" else "closed"
        else:
            status = "closed"
        conn.execute("UPDATE alerts SET status=? WHERE alert_id=?", (status, alert_id))
        conn.execute("INSERT INTO audit_log(actor,event_type,alert_id,details) VALUES (?, 'analyst_review', ?, ?)",
                     (analyst, alert_id, json.dumps({"verdict": verdict, "ai_decision": d["decision"],
                                                     "note": note, "new_status": status})))
        conn.commit()
        return {"alert_id": alert_id, "verdict": verdict, "ai_decision": d["decision"], "new_status": status}
    finally:
        if own:
            conn.close()


def history(alert_id: str, conn=None) -> list[dict]:
    """Everything that happened to one alert, in order. This is what an auditor asks for."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = conn.execute("""SELECT event_time, actor, event_type, details FROM audit_log
                               WHERE alert_id=? ORDER BY log_id""", (alert_id,)).fetchall()
        return [{**dict(r), "details": json.loads(r["details"] or "{}")} for r in rows]
    finally:
        if own:
            conn.close()


def simulate_reviews() -> dict:
    """DEMO ONLY: plays the analyst for every alert sent to a human, using ground truth.
    Shows the feedback loop: how often the humans agree with the AI's escalations."""
    conn = get_conn()
    rows = conn.execute("""SELECT a.alert_id, a.ground_truth, d.decision FROM alerts a
                           JOIN triage_decisions d USING(alert_id)
                           WHERE d.decision != 'auto_dismiss' AND d.analyst_override IS NULL""").fetchall()
    out = {"confirmed": 0, "overturned": 0}
    for r in rows:
        verdict = "confirmed" if r["ground_truth"] == "genuine_risk" else "overturned"
        note = "Suspicious activity confirmed" if verdict == "confirmed" else "Legitimate on review - closed"
        record_override(r["alert_id"], "demo-analyst", verdict, note, conn)
        out[verdict] += 1
    conn.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("history"); h.add_argument("alert_id")
    o = sub.add_parser("override"); o.add_argument("alert_id")
    o.add_argument("--analyst", required=True); o.add_argument("--verdict", choices=VERDICTS, required=True)
    o.add_argument("--note", default="")
    sub.add_parser("simulate-reviews")
    a = ap.parse_args()
    if a.cmd == "history":
        print(json.dumps(history(a.alert_id), indent=2, ensure_ascii=False))
    elif a.cmd == "override":
        print(json.dumps(record_override(a.alert_id, a.analyst, a.verdict, a.note), indent=2))
    else:
        r = simulate_reviews()
        print(f"Demo analyst reviewed {sum(r.values())} alerts: {r['confirmed']} confirmed as risk, "
              f"{r['overturned']} found legitimate.")


if __name__ == "__main__":
    main()
