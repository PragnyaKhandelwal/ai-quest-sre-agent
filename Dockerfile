FROM python:3.11-slim

WORKDIR /app

# curl is needed by docker-compose.yml's healthcheck to poll GET /health.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ ./agents/
COPY backend/ ./backend/

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
