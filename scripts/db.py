"""
SQLite schema for the alert triage layer.

Design notes (these are interview talking points):
- customers / counterparties / transactions = the CONTEXT the core AML system
  doesn't hand the analyst in one place. We assemble it.
- alerts = what Actimize/FICO already flagged. We never re-detect; we triage.
- triage_decisions = Claude's assessment + the routing outcome + any analyst override.
- audit_log = append-only. UPDATE/DELETE are blocked by triggers, so the trail
  can't be quietly edited after the fact (SAMA audit expectation).

Usage:  python -m scripts.db            # create tables
        python -m scripts.db --reset    # drop DB file and recreate
"""
import sqlite3
import sys

from scripts.config import DB_PATH

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
    customer_id        TEXT PRIMARY KEY,
    full_name          TEXT NOT NULL,
    customer_type      TEXT NOT NULL CHECK (customer_type IN ('individual','business')),
    nationality        TEXT,
    city               TEXT,
    occupation_or_sector TEXT,
    onboarded_date     TEXT NOT NULL,
    kyc_risk_rating    TEXT NOT NULL CHECK (kyc_risk_rating IN ('Low','Medium','High')),
    is_pep             INTEGER NOT NULL DEFAULT 0,       -- politically exposed person
    avg_monthly_volume_sar REAL                          -- expected profile from KYC
);

CREATE TABLE IF NOT EXISTS counterparties (
    counterparty_id    TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    counterparty_type  TEXT NOT NULL CHECK (counterparty_type IN ('individual','business')),
    country            TEXT NOT NULL,
    is_registered_business INTEGER NOT NULL DEFAULT 0,  -- e.g. valid CR number
    risk_rating        TEXT NOT NULL CHECK (risk_rating IN ('Low','Medium','High')),
    on_watchlist       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id     TEXT PRIMARY KEY,
    customer_id        TEXT NOT NULL REFERENCES customers(customer_id),
    counterparty_id    TEXT NOT NULL REFERENCES counterparties(counterparty_id),
    amount_sar         REAL NOT NULL,
    direction          TEXT NOT NULL CHECK (direction IN ('outbound','inbound')),
    channel            TEXT NOT NULL,                    -- mobile, branch, SARIE, SWIFT, card
    timestamp          TEXT NOT NULL,                    -- ISO 8601, Asia/Riyadh
    description        TEXT
);
CREATE INDEX IF NOT EXISTS idx_txn_customer_time ON transactions(customer_id, timestamp);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id           TEXT PRIMARY KEY,
    transaction_id     TEXT NOT NULL REFERENCES transactions(transaction_id),
    source_system      TEXT NOT NULL DEFAULT 'Actimize-SIM',
    alert_reason       TEXT NOT NULL CHECK (alert_reason IN
                         ('velocity','high_risk_country','amount_spike',
                          'counterparty_risk','structuring','dormant_reactivation')),
    alert_severity     TEXT NOT NULL CHECK (alert_severity IN ('Low','Medium','High')),
    created_at         TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'new' CHECK (status IN
                         ('new','auto_dismissed','flagged_for_review','escalated','closed')),
    -- Ground truth for the DEMO ONLY (lets us measure triage accuracy). A real bank
    -- would get this from historical analyst dispositions.
    ground_truth       TEXT CHECK (ground_truth IN ('false_positive','genuine_risk'))
);
CREATE INDEX IF NOT EXISTS idx_alert_status ON alerts(status);

CREATE TABLE IF NOT EXISTS triage_decisions (
    decision_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id           TEXT NOT NULL REFERENCES alerts(alert_id),
    assessment         TEXT NOT NULL CHECK (assessment IN ('false_positive','genuine_risk','uncertain')),
    confidence         REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    decision           TEXT NOT NULL CHECK (decision IN
                         ('auto_dismiss','flag_for_review','escalate_senior')),
    reasoning          TEXT NOT NULL,
    analyst_summary    TEXT,
    risk_factors       TEXT,                             -- JSON array
    model              TEXT NOT NULL,
    prompt_version     TEXT NOT NULL,
    latency_ms         INTEGER,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    analyst_override   TEXT CHECK (analyst_override IN ('confirmed','overturned')),
    override_by        TEXT,
    override_note      TEXT,
    decided_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_decision_alert ON triage_decisions(alert_id);

CREATE TABLE IF NOT EXISTS audit_log (
    log_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    actor              TEXT NOT NULL,                    -- 'system', 'claude', analyst id
    event_type         TEXT NOT NULL,                    -- alert_received, triaged, override...
    alert_id           TEXT,
    details            TEXT                              -- JSON payload
);

CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


TABLES = ["audit_log", "triage_decisions", "alerts", "transactions", "counterparties", "customers"]


def init_db(reset: bool = False) -> None:
    with get_conn() as conn:
        if reset:  # drop tables rather than deleting the file (file may be locked by a DB viewer)
            conn.execute("PRAGMA foreign_keys = OFF")
            for t in TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.executescript(SCHEMA)
    print(f"Database ready: {DB_PATH}")


if __name__ == "__main__":
    init_db(reset="--reset" in sys.argv)
