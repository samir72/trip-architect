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
#
# Shell-form CMD (not exec-form array) so $PORT expands: Hugging Face Spaces
# expects port 7860 with no PORT env var set, Render (and similar PaaS hosts)
# inject PORT and expect the app to bind to it -- this one image supports both.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1
