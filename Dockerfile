FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY README.md .

EXPOSE 7860

# --workers 1 is load-bearing, not a default left unconfigured: the plan/
# session stores are in-memory and process-local, so multiple workers would
# silently split traffic across inconsistent copies of the same state.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
