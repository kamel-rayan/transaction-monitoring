"""Audit trail + dashboard tests."""
import sqlite3

import pytest

from scripts.api import app
from scripts.audit_logger import history, record_override
from scripts.dashboard import collect
from scripts.db import get_conn
from scripts.triage_engine import triage_alert


def test_override_is_logged_and_history_is_ordered():
    triage_alert("ALT-2026-00007", mode="rules")
    out = record_override("ALT-2026-00007", "tester", "confirmed", "SAR filed")
    assert out["new_status"] == "closed"
    events = [e["event_type"] for e in history("ALT-2026-00007")]
    assert events[-1] == "analyst_review" and "triaged" in events


def test_audit_log_cannot_be_edited():
    conn = get_conn()
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE audit_log SET actor='hacker'")
    conn.close()


def test_dashboard_data_and_page():
    d = collect()
    assert d["kpi"]["triaged"] >= 1 and "decisions" in d
    r = app.test_client().get("/dashboard")
    assert r.status_code == 200 and b"AML Alert Triage" in r.data
