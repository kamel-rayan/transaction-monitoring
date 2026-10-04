"""
Triage engine HTTP API - this is what n8n (Step 4) calls.

Run:  python -m scripts.api        -> http://localhost:5000

  GET  /health                     status + current mode
  GET  /alerts?status=new&limit=20 list alerts (what the core AML system queued)
  GET  /alerts/<id>/context        the full case file for one alert
  POST /triage   {"alert_id": "ALT-2026-00009"}   triage one alert, returns the decision
  GET  /alerts/<id>/history        full audit trail for one alert
  POST /alerts/<id>/review  {"analyst": "...", "verdict": "confirmed|overturned", "note": "..."}
  GET  /dashboard                  live results dashboard
"""
from flask import Flask, jsonify, request

from scripts import config
from scripts.audit_logger import history, record_override
from scripts.dashboard import render
from scripts.context import get_alert_context
from scripts.db import get_conn
from scripts.triage_engine import triage_alert, triage_mode

app = Flask(__name__)


@app.get("/health")
def health():
    return jsonify(status="ok", mode=triage_mode(),
                   model=config.CLAUDE_MODEL if triage_mode() == "claude" else "rules-baseline-v1")


@app.get("/alerts")
def list_alerts():
    status = request.args.get("status", "new")
    limit = min(int(request.args.get("limit", 20)), 500)
    with get_conn() as conn:
        rows = conn.execute("""SELECT alert_id, transaction_id, alert_reason, alert_severity, created_at, status
                               FROM alerts WHERE status = ? ORDER BY created_at LIMIT ?""", (status, limit)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/alerts/<alert_id>/context")
def alert_context(alert_id):
    try:
        return jsonify(get_alert_context(alert_id))
    except KeyError as e:
        return jsonify(error=str(e)), 404


@app.post("/triage")
def triage():
    body = request.get_json(silent=True) or {}
    alert_id = body.get("alert_id")
    if not alert_id:
        return jsonify(error="alert_id is required"), 400
    try:
        return jsonify(triage_alert(alert_id))
    except KeyError as e:
        return jsonify(error=str(e)), 404


@app.get("/alerts/<alert_id>/history")
def alert_history(alert_id):
    return jsonify(history(alert_id))


@app.post("/alerts/<alert_id>/review")
def alert_review(alert_id):
    b = request.get_json(silent=True) or {}
    try:
        return jsonify(record_override(alert_id, b.get("analyst", "unknown"), b.get("verdict", ""), b.get("note", "")))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except KeyError as e:
        return jsonify(error=str(e)), 404


@app.get("/dashboard")
def dashboard():
    return render(), 200, {"Content-Type": "text/html; charset=utf-8"}


if __name__ == "__main__":
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT)
