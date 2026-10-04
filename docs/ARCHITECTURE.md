# Architecture

## Position in the bank's stack

```
Core banking ──▶ Transaction monitoring (Actimize / FICO) ──▶ ALERT
                                                               │
                                     ┌─────────────────────────▼─────────────────────────┐
                                     │  THIS PROJECT: triage layer                       │
                                     │  n8n  ──▶  Flask API  ──▶  Triage engine  ──▶  DB  │
                                     └─────────────────────────┬─────────────────────────┘
                                                               ▼
                                         Case management / analyst queues
```

The triage layer never re-detects and never changes what the monitoring system flags. It only decides **who looks at an alert, and how urgently**.

## Request flow (one alert)

1. **Trigger.** n8n receives an alert (webhook from the core system, or the batch trigger for demos) and calls `POST /triage`.
2. **Context.** `context.py` builds the case file from the database:
   - flagged transaction
   - customer profile (type, occupation, KYC rating, PEP, tenure)
   - counterparty (country, registration, risk rating, watchlist)
   - relationship history with this counterparty
   - 90-day behaviour (average amount, multiple vs average, 24h velocity, days idle)
   - last 10 transactions

   It uses **only transactions before the alert timestamp**, and **never includes ground truth**. Both are enforced by tests.
3. **Assessment.** `triage_engine.py` returns `{assessment, confidence, risk_factors, reasoning, analyst_summary}`:
   - `claude` mode sends the case file with a versioned system prompt (`prompt.py`) and parses strict JSON.
   - `rules` mode scores red flags minus mitigants. It's free, deterministic, and the baseline Claude is measured against.
4. **Guardrails** (in code, after the model): watchlist hit or FATF high-risk jurisdiction → senior analyst, regardless of the model.
5. **Routing.**

   | Assessment | Confidence | Route |
   |---|---|---|
   | false_positive | ≥ 0.80 | auto_dismiss |
   | genuine_risk | ≥ 0.70 | flag_for_review |
   | anything else | – | escalate_senior |

6. **Persistence.** The decision goes to `triage_decisions` (with model, prompt version, latency and tokens), the status goes to `alerts`, and events go to `audit_log`.
7. **Failure.** Any exception (API down, bad JSON) → `uncertain`, confidence 0 → senior analyst. The engine never auto-closes on failure.

## Data model (SQLite)

| Table | Role |
|---|---|
| `customers`, `counterparties`, `transactions` | Context. In production these would be read-only views on core banking data. |
| `alerts` | Input from the monitoring system. `ground_truth` exists only for demo scoring. |
| `triage_decisions` | One row per decision: assessment, confidence, route, reasoning, model, prompt version, tokens, analyst override |
| `audit_log` | **Append-only.** `BEFORE UPDATE` / `BEFORE DELETE` triggers abort any change. |

## Human feedback loop

Analysts confirm or overturn decisions (`POST /alerts/<id>/review`). Overturning an auto-dismissal re-opens the alert. Override rates per route, alert reason and prompt version are the production monitoring signal. A rising override rate means the model or the data has drifted.

## Production gaps (deliberately out of scope)

- SQLite → Postgres; Flask dev server → gunicorn behind auth
- Integration with the real alert feed and case-management system
- PII minimisation before any external model call, or a privately hosted model, per the bank's data-residency rules
- Model-risk documentation, validation and periodic re-testing on fresh labelled samples
