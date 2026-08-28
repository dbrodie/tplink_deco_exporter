FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home /nonexistent --shell /usr/sbin/nologin deco \
    && mkdir /data \
    && chown deco:deco /data
COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt
COPY tplink_deco_exporter/ ./tplink_deco_exporter/
COPY config.yml /config/config.yml
ENV CONFIG_PATH=/config/config.yml PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER deco
EXPOSE 9100
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 CMD ["curl", "--fail", "--silent", "http://127.0.0.1:9100/healthz"]
CMD ["python", "-m", "tplink_deco_exporter.main"]
