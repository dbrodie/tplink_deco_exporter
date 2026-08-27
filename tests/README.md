# Test suite

The tests exercise the exporter without requiring a live Deco or Loki instance.

## Coverage

- `test_api.py`: encryption helpers, name decoding, and the read-only public API surface.
- `test_config.py`: YAML and environment configuration, secret files, validation, and redaction.
- `test_metrics.py`: device, client, WAN/LAN, system, time, and wireless metric mappings plus stale-series removal.
- `test_logs_and_probes.py`: raw log ordering, duplicate records, anchors, restart recovery, partial Loki delivery, watermark state, and ICMP observations.

Run the suite with:

```shell
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest
```

Docker deployment is checked separately:

```shell
docker compose config
docker build -t tplink-deco-exporter:test .
```

Tests must not contact or mutate a real router. API fixtures may use `read` and the temporary log-snapshot `build` operation only.
