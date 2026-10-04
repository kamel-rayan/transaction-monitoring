# Deployment

## Option A: local (Windows)

Prerequisites: Python 3.10+, and Node.js LTS for n8n (`winget install Python.Python.3.12` · `winget install OpenJS.NodeJS.LTS`).

```powershell
cd transaction-monitoring
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
copy .env.example .env
python -m scripts.setup_check     # add --live to test a Claude API key
python -m scripts.generate_data
```

**Window 1, the triage API** (keep open):
```powershell
python -m scripts.api             # http://127.0.0.1:5000  ·  dashboard at /dashboard
```

**Window 2, n8n** (keep open):
```powershell
npx n8n                           # http://localhost:5678
```
In n8n: **⋯ → Import from File** → `n8n/aml_alert_triage_workflow.json`.

**Window 3, run the demo:**
```powershell
python -m scripts.run_batch --reset-only    # queue alerts for n8n
```
Then in n8n click **Execute workflow** (trigger: *Run demo batch*).

> The workflow uses `127.0.0.1`, not `localhost`. Node resolves `localhost` to IPv6 first, and the API listens on IPv4.

## Option B: Docker

```powershell
cd docker
docker compose up --build
```
- API + dashboard: http://localhost:5000/dashboard. On first start it generates and triages the demo data.
- n8n: http://localhost:5678. After import, change `127.0.0.1:5000` to `api:5000` in the two HTTP nodes.

To use Claude, set the key before starting: `$env:ANTHROPIC_API_KEY="sk-ant-..."`.

## Switching to Claude

1. Put `ANTHROPIC_API_KEY` in `.env`, and set `CLAUDE_MODEL` to a current model ID (see the Anthropic models page).
2. `python -m scripts.setup_check --live`
3. Start small: `python -m scripts.run_batch --limit 50`
4. Full run: `python -m scripts.run_batch`, then `python -m scripts.dashboard`

`TRIAGE_MODE=rules` forces practice mode even when a key is set.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `TRIAGE_MODE` | auto | `rules`, `claude`, or empty (Claude if a key is set) |
| `CLAUDE_MODEL` | `claude-sonnet-4-5` | Model ID for triage |
| `AUTO_DISMISS_MIN_CONFIDENCE` | 0.80 | Minimum confidence to auto-close a false positive |
| `REVIEW_MIN_CONFIDENCE` | 0.70 | Minimum confidence to send a genuine risk to the analyst queue |
| `MANUAL_REVIEW_MINUTES` / `ASSISTED_REVIEW_MINUTES` | 30 / 5 | Dashboard workload assumptions |
| `ANALYST_ANNUAL_COST_USD` | 150000 | Dashboard cost assumption |
