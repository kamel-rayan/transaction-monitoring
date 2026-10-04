"""
Business case calculator - one formula, three scenarios, so every number in
docs/BUSINESS_CASE.md can be reproduced and challenged.

Usage:  python -m scripts.business_case
        python -m scripts.business_case --alerts-per-day 500 --analyst-cost 70000
"""
import argparse

HOURS_PER_FTE_YEAR = 1800      # productive hours per analyst per year
WORKING_DAYS = 250
MANUAL_MIN = 30                 # today: minutes to triage one alert by hand
QA_MIN = 10                     # minutes to QA-check one auto-closed alert

SCENARIOS = {
    #               auto-close rate, assisted review min, QA sample of auto-closed
    "Conservative": (0.50, 20, 0.10),
    "Base":         (0.68, 10, 0.05),
    "Optimistic":   (0.70, 5, 0.02),
}


def scenario(alerts: int, auto_rate: float, assisted_min: float, qa_rate: float) -> dict:
    manual_h = alerts * MANUAL_MIN / 60
    auto = alerts * auto_rate
    human_h = (alerts - auto) * assisted_min / 60
    qa_h = auto * qa_rate * QA_MIN / 60
    after_h = human_h + qa_h
    return {"manual_h": manual_h, "after_h": after_h, "saved_h": manual_h - after_h,
            "saved_pct": 1 - after_h / manual_h}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alerts-per-day", type=int, default=300)
    ap.add_argument("--analyst-cost", type=float, default=150000, help="fully loaded USD per analyst per year")
    a = ap.parse_args()
    yearly = a.alerts_per_day * WORKING_DAYS

    print(f"Volume: {a.alerts_per_day} alerts/day x {WORKING_DAYS} days = {yearly:,} alerts/year")
    print(f"Today: {yearly * MANUAL_MIN / 60:,.0f} analyst hours/year "
          f"= {yearly * MANUAL_MIN / 60 / HOURS_PER_FTE_YEAR:.1f} FTE\n")
    print(f"{'Scenario':<14}{'Auto-close':>11}{'Review min':>11}{'QA':>6}{'Time saved':>12}{'FTE freed':>11}{'USD/year':>13}")
    for name, (r, m, q) in SCENARIOS.items():
        s = scenario(yearly, r, m, q)
        fte = s["saved_h"] / HOURS_PER_FTE_YEAR
        print(f"{name:<14}{r:>10.0%}{m:>11}{q:>6.0%}{s['saved_pct']:>12.0%}{fte:>11.1f}{fte * a.analyst_cost:>13,.0f}")


if __name__ == "__main__":
    main()
