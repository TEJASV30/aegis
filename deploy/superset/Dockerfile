FROM apache/superset:4.1.1

USER root
RUN pip install --no-cache-dir psycopg2-binary
COPY deploy/superset/superset_config.py /app/pythonpath/superset_config.py
COPY deploy/superset/bootstrap_aegis.py /app/pythonpath/bootstrap_aegis.py
USER superset
