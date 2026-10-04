"""
Step 1 verification. Run:  python -m scripts.setup_check
Add --live to make one real (tiny) Claude API call.
"""
import importlib
import sqlite3
import sys

OK, FAIL, WARN = "[ OK ]", "[FAIL]", "[WARN]"
failures = 0


def check(label, ok, detail="", warn=False):
    global failures
    tag = OK if ok else (WARN if warn else FAIL)
    if not ok and not warn:
        failures += 1
    print(f"{tag} {label}{' - ' + detail if detail else ''}")


# 1. Python
check("Python >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0])

# 2. Packages
for pkg in ["anthropic", "flask", "pandas", "dotenv", "requests"]:
    try:
        importlib.import_module(pkg)
        check(f"package: {pkg}", True)
    except ImportError:
        check(f"package: {pkg}", False, "run: pip install -r requirements.txt")

# 3. Config
try:
    from scripts import config
    check(".env loaded", (config.ROOT / ".env").exists(),
          "copy .env.example to .env", warn=True)
    key_ok = config.ANTHROPIC_API_KEY.startswith("sk-ant-") and "xxxx" not in config.ANTHROPIC_API_KEY
    check("ANTHROPIC_API_KEY set", key_ok, "needed from Step 3 onward", warn=True)
    check("thresholds sane",
          0 < config.REVIEW_MIN_CONFIDENCE <= config.AUTO_DISMISS_MIN_CONFIDENCE <= 1,
          f"review>={config.REVIEW_MIN_CONFIDENCE}, dismiss>={config.AUTO_DISMISS_MIN_CONFIDENCE}")
except Exception as e:  # noqa: BLE001
    check("config import", False, str(e))
    sys.exit(1)

# 4. Database + append-only audit log
try:
    from scripts.db import get_conn, init_db
    init_db()
    with get_conn() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    need = {"customers", "counterparties", "transactions", "alerts", "triage_decisions", "audit_log"}
    check("schema tables", need <= tables, ", ".join(sorted(need - tables)) or "all 6 present")

    conn = get_conn()
    conn.execute("INSERT INTO audit_log(actor, event_type, details) VALUES ('system','setup_check','{}')")
    conn.commit()
    try:
        conn.execute("DELETE FROM audit_log")
        conn.commit()
        check("audit_log append-only", False, "DELETE was allowed")
    except sqlite3.DatabaseError:
        conn.rollback()
        check("audit_log append-only", True, "UPDATE/DELETE blocked")
    conn.close()
except Exception as e:  # noqa: BLE001
    check("database", False, str(e))

# 5. Optional live API call
if "--live" in sys.argv:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        r = client.messages.create(model=config.CLAUDE_MODEL, max_tokens=10,
                                   messages=[{"role": "user", "content": "Reply with: ready"}])
        check(f"Claude API ({config.CLAUDE_MODEL})", True, r.content[0].text.strip())
    except Exception as e:  # noqa: BLE001
        check(f"Claude API ({config.CLAUDE_MODEL})", False, str(e)[:150])

print("\nStep 1 complete." if failures == 0 else f"\n{failures} check(s) failed.")
sys.exit(1 if failures else 0)
