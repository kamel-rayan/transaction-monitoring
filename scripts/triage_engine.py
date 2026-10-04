"""
Step 3 - The triage engine.

For one alert:  build context -> assess (rules OR Claude) -> apply guardrails -> route
                -> save decision -> update alert status -> write audit log.

Two assessment modes (TRIAGE_MODE in .env):
  rules   Free practice mode. A transparent red-flag/mitigant scorecard. No API needed.
          Also serves as the BASELINE to compare Claude against.
  claude  Real AI triage via the Claude API (needs ANTHROPIC_API_KEY).
Default: claude if a real API key is set, otherwise rules.

Design choices that matter in a compliance interview:
  * Fail-safe: any error -> escalate to a senior analyst. Never auto-dismiss on failure.
  * Guardrails override the AI: watchlist hits and FATF high-risk jurisdictions always
    go to a senior analyst, whatever the model says.
  * Every decision stores model, prompt version, confidence and reasoning.
"""
import json
import os
import re
import time
from datetime import datetime

from scripts import config
from scripts.context import get_alert_context
from scripts.db import get_conn
from scripts.prompt import PROMPT_VERSION, SYSTEM, build_triage_prompt

HIGH_RISK_JURISDICTIONS = {"IR", "KP", "MM"}
RULES_VERSION = "rules-baseline-v1"


def triage_mode() -> str:
    mode = os.getenv("TRIAGE_MODE", "").strip().lower()
    if mode in ("rules", "claude"):
        return mode
    key = config.ANTHROPIC_API_KEY
    return "claude" if key.startswith("sk-ant-") and "xxxx" not in key else "rules"


# ---------------------------------------------------------------- rules mode
def assess_rules(ctx: dict) -> dict:
    """Score red flags (+) and mitigants (-). Transparent, deterministic, explainable."""
    t, cust, cp = ctx["flagged_transaction"], ctx["customer"], ctx["counterparty"]
    rel, beh = ctx["relationship"], ctx["customer_behaviour_90d"]
    at = datetime.fromisoformat(t["timestamp"])
    recent = ctx["recent_transactions"]
    flags, mitig = [], []

    def hours_before(x):
        return (at - datetime.fromisoformat(x["timestamp"])).total_seconds() / 3600

    clean_cp = cp["is_registered_business"] and cp["risk_rating"] == "Low"
    rel_age = (at - datetime.fromisoformat(rel["first_seen"])).days if rel["first_seen"] else 0
    established = rel["prior_transactions_with_counterparty"] >= 2 and rel_age >= 30

    if cp["on_watchlist"]:
        flags.append((100, "counterparty on watchlist"))
    if cp["country"] in HIGH_RISK_JURISDICTIONS:
        flags.append((60, f"FATF high-risk jurisdiction ({cp['country']})"))
    if cp["risk_rating"] == "High":
        flags.append((25, "high-risk counterparty"))
    if cp["counterparty_type"] == "business" and not cp["is_registered_business"]:
        flags.append((15, "unregistered business counterparty"))
    if rel["prior_transactions_with_counterparty"] == 0:
        flags.append((15, "first-ever transaction with this counterparty"))
    if t["amount_sar"] > 10 * (cust["avg_monthly_volume_sar"] or 1) and not clean_cp:
        flags.append((30, "amount far exceeds customer's expected monthly volume"))
    near = [x for x in recent if 9000 <= x["amount_sar"] < 10000 and hours_before(x) <= 72]
    if len(near) >= 3 and 9000 <= t["amount_sar"] < 10000:
        flags.append((50, f"possible structuring: {len(near) + 1} transfers just under 10,000 SAR in 72h"))
    inbound_24h = {x["counterparty"] for x in recent if x["direction"] == "inbound" and hours_before(x) <= 24}
    if t["direction"] == "outbound" and len(inbound_24h) >= 4:
        flags.append((40, f"pass-through pattern: funds from {len(inbound_24h)} senders in 24h then sent out"))
    if (beh["days_since_previous_activity"] or 0) >= 45 and cp["country"] != "SA":
        flags.append((30, "dormant account suddenly sending abroad"))
    if not t["description"]:
        flags.append((5, "no payment description"))

    if established:
        mitig.append((30, f"established relationship ({rel['prior_transactions_with_counterparty']} prior payments since {rel['first_seen'][:10]})"))
    if clean_cp and cp["country"] == "SA":
        mitig.append((25, "registered low-risk Saudi counterparty"))
    if cust["tenure_years"] >= 3:
        mitig.append((5, f"long-standing customer ({cust['tenure_years']} years)"))

    score = sum(w for w, _ in flags) - sum(w for w, _ in mitig)
    if score <= -10:
        assessment, conf = "false_positive", min(0.97, 0.80 + (-score - 10) / 200)
    elif score >= 40:
        assessment, conf = "genuine_risk", min(0.97, 0.70 + (score - 40) / 200)
    else:
        assessment, conf = "uncertain", 0.55 + abs(score - 15) / 250

    why = "; ".join(f for _, f in flags) or "none"
    ok = "; ".join(m for _, m in mitig) or "none"
    summary = {
        "false_positive": f"Likely legitimate: {mitig[0][1] if mitig else 'consistent with profile'}",
        "genuine_risk": f"Review: {flags[0][1] if flags else 'risk indicators present'}",
        "uncertain": "Mixed signals - needs analyst judgement",
    }[assessment]
    return {"assessment": assessment, "confidence": round(conf, 2),
            "risk_factors": [f for _, f in flags],
            "reasoning": f"Score {score:+d}. Red flags: {why}. Mitigants: {ok}.",
            "analyst_summary": summary, "model": RULES_VERSION}


# ---------------------------------------------------------------- claude mode
def assess_claude(ctx: dict) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = client.messages.create(model=config.CLAUDE_MODEL, max_tokens=config.CLAUDE_MAX_TOKENS,
                                  system=SYSTEM, messages=[{"role": "user", "content": build_triage_prompt(ctx)}])
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"No JSON in model output: {text[:200]}")
    out = json.loads(m.group(0))
    if out.get("assessment") not in ("false_positive", "genuine_risk", "uncertain"):
        raise ValueError(f"Bad assessment value: {out.get('assessment')}")
    out["confidence"] = max(0.0, min(1.0, float(out.get("confidence", 0))))
    out["model"] = config.CLAUDE_MODEL
    out["_usage"] = (resp.usage.input_tokens, resp.usage.output_tokens)
    return out


# ---------------------------------------------------------------- routing
def route(assessment: str, confidence: float, ctx: dict | None = None) -> tuple[str, str | None]:
    """Returns (decision, guardrail_reason)."""
    if ctx:
        cp = ctx["counterparty"]
        if cp["on_watchlist"]:
            return "escalate_senior", "guardrail: watchlist hit always escalates"
        if cp["country"] in HIGH_RISK_JURISDICTIONS:
            return "escalate_senior", "guardrail: FATF high-risk jurisdiction always escalates"
    if assessment == "false_positive" and confidence >= config.AUTO_DISMISS_MIN_CONFIDENCE:
        return "auto_dismiss", None
    if assessment == "genuine_risk" and confidence >= config.REVIEW_MIN_CONFIDENCE:
        return "flag_for_review", None
    return "escalate_senior", None


STATUS = {"auto_dismiss": "auto_dismissed", "flag_for_review": "flagged_for_review",
          "escalate_senior": "escalated"}


def triage_alert(alert_id: str, mode: str | None = None) -> dict:
    mode = mode or triage_mode()
    conn = get_conn()
    try:
        ctx = get_alert_context(alert_id, conn)
        conn.execute("INSERT INTO audit_log(actor,event_type,alert_id,details) VALUES ('system','alert_received',?,?)",
                     (alert_id, json.dumps({"reason": ctx["alert"]["alert_reason"], "mode": mode})))
        start = time.perf_counter()
        try:
            res = assess_claude(ctx) if mode == "claude" else assess_rules(ctx)
        except Exception as e:  # fail safe: a human looks at it
            res = {"assessment": "uncertain", "confidence": 0.0, "risk_factors": ["triage_error"],
                   "reasoning": f"Automated triage failed ({type(e).__name__}: {str(e)[:150]}). Routed to senior analyst.",
                   "analyst_summary": "Automated triage failed - manual review required",
                   "model": config.CLAUDE_MODEL if mode == "claude" else RULES_VERSION}
        latency = int((time.perf_counter() - start) * 1000)
        decision, guardrail = route(res["assessment"], res["confidence"], ctx)
        if guardrail:
            res["reasoning"] += f" [{guardrail}]"
        tokens = res.pop("_usage", (None, None))

        conn.execute("""INSERT INTO triage_decisions(alert_id, assessment, confidence, decision, reasoning,
                            analyst_summary, risk_factors, model, prompt_version, latency_ms, input_tokens, output_tokens)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (alert_id, res["assessment"], res["confidence"], decision, res["reasoning"],
                      res.get("analyst_summary"), json.dumps(res.get("risk_factors", []), ensure_ascii=False),
                      res["model"], PROMPT_VERSION if mode == "claude" else RULES_VERSION,
                      latency, tokens[0], tokens[1]))
        conn.execute("UPDATE alerts SET status=? WHERE alert_id=?", (STATUS[decision], alert_id))
        conn.execute("INSERT INTO audit_log(actor,event_type,alert_id,details) VALUES (?, 'triaged', ?, ?)",
                     ("claude" if mode == "claude" else "rules_engine", alert_id,
                      json.dumps({"decision": decision, "assessment": res["assessment"],
                                  "confidence": res["confidence"], "model": res["model"]})))
        conn.commit()
        return {"alert_id": alert_id, "decision": decision, "assessment": res["assessment"],
                "confidence": res["confidence"], "reasoning": res["reasoning"],
                "analyst_summary": res.get("analyst_summary"), "risk_factors": res.get("risk_factors", []),
                "model": res["model"], "latency_ms": latency}
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    print(json.dumps(triage_alert(sys.argv[1] if len(sys.argv) > 1 else "ALT-2026-00009"),
                     indent=2, ensure_ascii=False))
