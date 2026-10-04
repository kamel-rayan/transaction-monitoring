"""
Step 5 - Results dashboard (single self-contained HTML file, no internet needed).

Usage:  python -m scripts.dashboard          -> writes reports/dashboard.html and opens it
        (also served live by the API at http://127.0.0.1:5000/dashboard)
"""
import json
import webbrowser
from datetime import datetime
from pathlib import Path

from scripts import config
from scripts.db import get_conn

TEMPLATE = Path(__file__).with_name("dashboard_template.html")
OUT = config.ROOT / "reports" / "dashboard.html"
HOURS_PER_FTE_YEAR = 1800


def collect() -> dict:
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("""
        SELECT a.alert_id, a.alert_reason, a.alert_severity, a.ground_truth, a.status,
               c.full_name, c.occupation_or_sector, t.amount_sar, t.direction,
               d.decision, d.assessment, d.confidence, d.analyst_summary, d.reasoning,
               d.model, d.analyst_override, d.override_note
        FROM alerts a
        JOIN triage_decisions d USING(alert_id)
        JOIN transactions t USING(transaction_id)
        JOIN customers c USING(customer_id)
        ORDER BY a.alert_id""").fetchall()]
    total_alerts = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    audit_events = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    conn.close()

    n = len(rows)
    count = lambda pred: sum(1 for r in rows if pred(r))
    dismissed = count(lambda r: r["decision"] == "auto_dismiss")
    review = count(lambda r: r["decision"] == "flag_for_review")
    escalated = count(lambda r: r["decision"] == "escalate_senior")
    to_human = review + escalated
    hours_before = n * config.MANUAL_REVIEW_MINUTES / 60
    hours_after = to_human * config.ASSISTED_REVIEW_MINUTES / 60
    hourly = config.ANALYST_ANNUAL_COST_USD / HOURS_PER_FTE_YEAR

    genuine = [r for r in rows if r["ground_truth"] == "genuine_risk"]
    fps = [r for r in rows if r["ground_truth"] == "false_positive"]
    reviewed = [r for r in rows if r["analyst_override"]]

    reasons = sorted({r["alert_reason"] for r in rows})
    by_reason = [{"reason": rs,
                  "dismissed": count(lambda r, rs=rs: r["alert_reason"] == rs and r["decision"] == "auto_dismiss"),
                  "review": count(lambda r, rs=rs: r["alert_reason"] == rs and r["decision"] == "flag_for_review"),
                  "escalated": count(lambda r, rs=rs: r["alert_reason"] == rs and r["decision"] == "escalate_senior")}
                 for rs in reasons]
    by_reason.sort(key=lambda x: -(x["dismissed"] + x["review"] + x["escalated"]))

    model = rows[0]["model"] if rows else "-"
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "model": model,
        "practice_mode": model.startswith("rules"),
        "total_alerts": total_alerts,
        "kpi": {"triaged": n, "dismissed": dismissed, "review": review, "escalated": escalated,
                "hours_before": round(hours_before, 1), "hours_after": round(hours_after, 1),
                "hours_saved": round(hours_before - hours_after, 1),
                "saved_pct": round(100 * (1 - hours_after / hours_before)) if n else 0,
                "cost_saved_usd": round((hours_before - hours_after) * hourly),
                "audit_events": audit_events},
        "accuracy": {"genuine_total": len(genuine),
                     "genuine_caught": sum(1 for r in genuine if r["decision"] != "auto_dismiss"),
                     "fp_total": len(fps),
                     "fp_cleared": sum(1 for r in fps if r["decision"] == "auto_dismiss")},
        "reviews": {"total": len(reviewed),
                    "confirmed": sum(1 for r in reviewed if r["analyst_override"] == "confirmed"),
                    "overturned": sum(1 for r in reviewed if r["analyst_override"] == "overturned")},
        "assumptions": {"manual_min": config.MANUAL_REVIEW_MINUTES, "assisted_min": config.ASSISTED_REVIEW_MINUTES,
                        "hourly_usd": round(hourly)},
        "by_reason": by_reason,
        "decisions": rows,
    }


def render() -> str:
    data = json.dumps(collect(), ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", data)


def main():
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    print(f"Dashboard written: {OUT}")
    try:
        webbrowser.open(OUT.as_uri())
    except Exception:
        pass


if __name__ == "__main__":
    main()
