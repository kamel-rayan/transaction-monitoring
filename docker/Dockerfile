FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scripts/ scripts/
COPY tests/ tests/
COPY n8n/ n8n/
COPY .env.example .env.example
RUN mkdir -p data reports
EXPOSE 5000
# First start: generate the demo bank and triage it, then serve the API + dashboard.
CMD ["sh", "-c", "[ -f data/triage.db ] || (python -m scripts.generate_data && python -m scripts.run_batch && python -m scripts.audit_logger simulate-reviews); python -m scripts.api"]
