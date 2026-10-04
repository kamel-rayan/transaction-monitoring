"""Triage engine tests. Run after: python -m scripts.generate_data"""
from scripts import triage_engine as te
from scripts.api import app
from scripts.context import get_alert_context


def test_routing_thresholds():
    assert te.route("false_positive", 0.85)[0] == "auto_dismiss"
    assert te.route("false_positive", 0.75)[0] == "escalate_senior"   # not confident enough to dismiss
    assert te.route("genuine_risk", 0.75)[0] == "flag_for_review"
    assert te.route("uncertain", 0.99)[0] == "escalate_senior"


def test_guardrail_overrides_ai():
    ctx = {"counterparty": {"on_watchlist": True, "country": "SA"}}
    assert te.route("false_positive", 0.99, ctx) == ("escalate_senior", "guardrail: watchlist hit always escalates")


def test_failure_never_auto_dismisses(monkeypatch):
    def boom(ctx):
        raise RuntimeError("API down")
    monkeypatch.setattr(te, "assess_rules", boom)
    out = te.triage_alert("ALT-2026-00009", mode="rules")
    assert out["decision"] == "escalate_senior"
    assert "failed" in out["reasoning"]


def test_rules_assessment_is_explainable():
    res = te.assess_rules(get_alert_context("ALT-2026-00009"))
    assert res["assessment"] in ("false_positive", "genuine_risk", "uncertain")
    assert 0 <= res["confidence"] <= 1 and res["reasoning"]


def test_api():
    c = app.test_client()
    assert c.get("/health").json["status"] == "ok"
    r = c.post("/triage", json={"alert_id": "ALT-2026-00009"})
    assert r.status_code == 200 and r.json["decision"] in ("auto_dismiss", "flag_for_review", "escalate_senior")
    assert c.post("/triage", json={}).status_code == 400
    assert c.get("/alerts/ALT-NOPE/context").status_code == 404
