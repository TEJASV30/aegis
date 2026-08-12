FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/artifacts /app/data /app/reports /mlartifacts \
    && chown -R appuser:appuser /app /mlartifacts
USER appuser

EXPOSE 8000
CMD ["uvicorn", "fraud_platform.serving.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test
USER root
RUN pip install --no-cache-dir ".[dev]"
COPY tests ./tests
COPY airflow ./airflow
COPY load_tests ./load_tests
USER appuser
CMD ["pytest", "-q"]

FROM runtime AS production
