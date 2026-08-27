# TP-Link Deco metrics and raw log forwarder

A read-only Prometheus exporter and Loki forwarder for TP-Link Deco firmware. It exports low-level API observations and forwards the router's `feedback_log` lines unchanged. It deliberately contains no health classification, alert rules, event categorization, or diagnosis.

## Docker Compose

The published image is `ghcr.io/dbrodie/tplink_deco_exporter`. Because the GitHub repository is private, authenticate Docker with a classic personal access token containing `read:packages` before the first pull:

```shell
export CR_PAT="your-github-token"
printf '%s' "$CR_PAT" | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

Then create the Docker secret and start the one-service stack:

```shell
mkdir -p .secrets
printf '%s' 'your-deco-password' > .secrets/deco_password
chmod 600 .secrets/deco_password
docker compose up -d
```

Edit `config.yml` for the Deco host, instance name, and Loki URL. Set `logs.enabled: true` when Loki is available. Metrics are served at `http://localhost:9100/metrics`; liveness and readiness are `/healthz` and `/readyz`.

The named `deco-state` volume persists `/data/log-watermark.json`. To use a host directory instead, replace `deco-state:/data` with `./data:/data`. `/data` is the only writable application directory. ICMP probing uses the container's `NET_RAW` capability and can be disabled.

For Loki authentication, add a Compose secret and configure one of `loki.password_file` (with `loki.username`) or `loki.bearer_token_file`. `loki.tenant_id` supplies `X-Scope-OrgID`. Passwords and tokens may also be supplied through `DECO_*` variables, but secret files are preferred.

## Configuration

Every YAML field can be overridden as `DECO_<SECTION>_<FIELD>`, for example `DECO_API_HOST`, `DECO_LOGS_ENABLED`, `DECO_LOKI_URL`, and `DECO_SERVER_PORT`. `DECO_INSTANCE` sets the top-level instance. `CONFIG_PATH` selects the YAML file.

The supported sections and fields are:

- `api`: `host`, `username`, `password`/`password_file`, `verify_ssl`, `timeout_seconds`, `timeout_retries`
- `metrics`: `collection_interval`
- `probes`: `enabled`, `interval`, `attempts`, `timeout`
- `logs`: `enabled`, `poll_interval`, `level`, `timezone_fallback`, `page_size`, `state_path`
- `loki`: `url`, `tenant_id`, `username`, `password`/`password_file`, `bearer_token`/`bearer_token_file`, `verify_ssl`, `timeout_seconds`, `batch_size`, `retries`
- `server`: `host`, `port`
- `logging`: `level`

Firmware log levels are `ALL`, `ALERT`, `CRITICAL`, `ERROR`, `WARNING`, `NOTICE`, `INFO`, and `DEBUG`.

## Exported data

The exporter maps the tested device inventory, per-node client inventory and rates, gateway CPU/memory ratios, system mode, IPv4/IPv6 internet state, WAN/LAN addressing, router time settings, and wireless operation/DFS/802.11r/beamforming/ERP flags. Device signal levels and all status strings remain raw firmware values.

Collection mechanics are observable through `deco_api_*`, `deco_collection_*`, device/client counts, probe metrics, log snapshot/record metrics, Loki delivery counters, and watermark-reset counters. `deco_api_endpoint_supported{endpoint}` reports both supported and probed optional firmware forms; an unsupported optional form is not treated as mesh failure.

The WAN username returned inside `wan_ipv4` is neither mapped nor logged. Unknown response fields are kept only for the current poll, and debug diagnostics report field names/types rather than values.

## Raw log replay

Each poll builds the firmware's temporary `feedback_log` snapshot, follows its page-count semantics, and searches backward for the saved eight-line SHA-256 anchor. New lines are reversed into chronological order and sent exactly as returned using only these Loki labels:

```text
job="tplink_deco", instance="<configured>", source="deco_router"
```

Only the leading router timestamp is parsed for Loki ordering. State is atomically replaced after every accepted Loki batch. If Loki is unavailable the prior watermark remains; there is intentionally no durable raw-log spool.

## Development

```shell
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest
docker build -t tplink-deco-exporter:dev .
```

## Publishing images

GitHub Actions publishes `linux/amd64` images to GitHub Container Registry. Every push to `main` updates the `latest`, `main`, and commit-SHA tags. A tag such as `v1.2.3` also publishes `v1.2.3`, `1.2.3`, and `1.2`. The workflow runs the tests before publishing and includes OCI provenance and SBOM attestations.

No registry password is stored in the repository. The workflow uses GitHub's short-lived `GITHUB_TOKEN` with `packages: write`. The package is linked to this repository through its OCI source label and initially inherits the private repository's access. To allow anonymous pulls, change the package visibility to public in the package settings on GitHub.

The exporter only calls read operations plus the temporary `feedback_log` `build` operation. It contains no router control or persistent configuration calls.
