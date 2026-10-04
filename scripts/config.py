"""Central config. Every script imports settings from here, never from os.environ directly."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:  # data scripts still run without it
    pass


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", 500))

DB_PATH = ROOT / os.getenv("DB_PATH", "data/triage.db")
DATA_DIR = ROOT / "data"

FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", 5000))

AUTO_DISMISS_MIN_CONFIDENCE = _f("AUTO_DISMISS_MIN_CONFIDENCE", 0.80)
REVIEW_MIN_CONFIDENCE = _f("REVIEW_MIN_CONFIDENCE", 0.70)

MANUAL_REVIEW_MINUTES = _f("MANUAL_REVIEW_MINUTES", 30)
ASSISTED_REVIEW_MINUTES = _f("ASSISTED_REVIEW_MINUTES", 5)
ANALYST_ANNUAL_COST_USD = _f("ANALYST_ANNUAL_COST_USD", 150000)
