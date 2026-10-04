# Business Case

## The problem

Transaction monitoring systems are tuned for recall: missing a real case is a regulatory failure, so they flag generously. The result is a queue where most alerts are false positives (industry estimates commonly run 70–95%), and every one must be reviewed and documented by a compliance analyst.

The cost is not detection. **It's the human hours spent clearing alerts that were never risky**, plus slower handling of the ones that are.

## The model

One formula, run by `python -m scripts.business_case`:

```
hours today   = alerts × manual review minutes
hours after   = (alerts not auto-closed × assisted review minutes)
              + (auto-closed alerts × QA sample rate × QA minutes)
time saved    = 1 − hours after / hours today
FTE freed     = hours saved / 1,800 productive hours per analyst-year
```

Auto-closed alerts are not left unchecked. A QA sample is re-reviewed by a person, which is how a bank would govern this in practice.

## Scenarios

Example bank: **300 alerts/day × 250 working days = 75,000 alerts/year.** At 30 minutes each, that's **37,500 analyst hours ≈ 20.8 FTE** today.

| Scenario | Auto-close rate | Assisted review | QA sample | Time saved | FTE freed |
|---|---|---|---|---|---|
| **Conservative** | 50% | 20 min | 10% | **65%** | 13.5 |
| Base | 68% | 10 min | 5% | 88% | 18.4 |
| Optimistic | 70% | 5 min | 2% | 95% | 19.7 |

**Annual value** depends on fully loaded analyst cost, which varies widely by market and seniority:

| Scenario | at $60k / analyst | at $150k / analyst |
|---|---|---|
| Conservative | $0.8M | $2.0M |
| Base | $1.1M | $2.8M |
| Optimistic | $1.2M | $3.0M |

Run your own numbers: `python -m scripts.business_case --alerts-per-day 500 --analyst-cost 70000`

## Cost of the AI

Each triage call sends roughly 1.5k tokens of case file and receives about 300 tokens. At current small-to-mid model pricing that's a fraction of a US cent to about a cent per alert. That comes to **hundreds of dollars a year** at 75,000 alerts, which is negligible next to the labour cost. Check current pricing before quoting a figure.

## How to read these numbers

- **The headline claim is the conservative one: about 65% less analyst time.** The other scenarios show the upside if assisted review gets faster.
- The auto-close rate must come from a **measured run** (see the README results table), not from these assumptions.
- **What actually matters is genuine risks auto-closed.** Time savings mean nothing if real risks slip through, which is why every result reports missed risks first.
- Freed capacity usually goes to backlog, investigation depth and SLA improvement rather than headcount reduction. Present it as capacity, not cuts.
