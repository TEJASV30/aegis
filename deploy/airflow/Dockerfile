FROM apache/airflow:2.10.3-python3.11

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

USER airflow
COPY pyproject.toml README.md LICENSE /opt/fraud-platform/
COPY src /opt/fraud-platform/src
COPY deploy/airflow/constraints.txt /opt/fraud-platform/airflow-constraints.txt
RUN pip install --no-cache-dir \
        --constraint /opt/fraud-platform/airflow-constraints.txt \
        /opt/fraud-platform \
        psycopg2-binary \
    && pip check
