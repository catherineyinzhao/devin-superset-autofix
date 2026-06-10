FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY docs ./docs

RUN mkdir -p /data
EXPOSE 8000

# The independent statistical validator's REAL mode also needs git + the
# Superset dev env to clone and re-run the suite; the container ships the
# orchestrator + mock-mode demo. Real validation runs where that env exists.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
