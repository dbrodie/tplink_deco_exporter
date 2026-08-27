import json
from unittest.mock import AsyncMock, patch

import pytest
from prometheus_client import CollectorRegistry

from tplink_deco_exporter.config import LogsConfig, LokiConfig, ProbesConfig
from tplink_deco_exporter.log_forwarder import (
    LogForwarder,
    StateStore,
    Watermark,
    line_hash,
)
from tplink_deco_exporter.probes import NodeProber
from tplink_deco_exporter.prometheus_metrics import DecoMetrics


def line(second, body):
    return f"Thu Aug 27 12:00:{second:02d} 2026 daemon.info test: {body}"


class LogApi:
    def __init__(self, pages):
        self.pages = pages
        self.build_levels = []

    async def async_request(self, path, form):
        return {"types": [{"name": "INFO", "value": 6}]}

    async def async_build_log(self, level):
        self.build_levels.append(level)
        return {}

    async def async_log_page(self, index, limit):
        return {
            "currentIndex": index,
            "totalNum": len(self.pages),
            "logList": [{"content": x} for x in self.pages[index]],
        }


class Loki:
    def __init__(self, batch_size=2, fail_after=None):
        self.config = LokiConfig(url="http://loki", batch_size=batch_size)
        self.lines = []
        self.calls = 0
        self.fail_after = fail_after

    async def push(self, records):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            return False
        self.lines.extend(line for _, line in records)
        return True


@pytest.mark.asyncio
async def test_first_replay_anchor_restart_and_exact_raw_lines(tmp_path):
    old = [line(1, "same"), line(1, "same"), line(2, "third"), line(3, "fourth")]
    api = LogApi([old[2:], old[:2]])
    loki = Loki()
    metrics = DecoMetrics(CollectorRegistry())
    config = LogsConfig(
        enabled=True, state_path=tmp_path / "watermark.json", page_size=2
    )
    forwarder = LogForwarder(api, loki, config, "mesh", metrics)
    await forwarder.poll("Asia/Jerusalem")
    assert loki.lines == old
    state = json.loads(config.state_path.read_text())
    assert state["anchor_hashes"] == [line_hash(x) for x in old]
    assert set(state) == {
        "version",
        "identity",
        "final_router_timestamp",
        "anchor_hashes",
        "last_loki_push_time",
    }
    newer = line(4, "new unchanged  ")
    api.pages = [[old[2], old[3], newer], old[:2]]
    loki.lines.clear()
    await forwarder.poll("Asia/Jerusalem")
    assert loki.lines == [newer]


@pytest.mark.asyncio
async def test_partial_loki_delivery_updates_only_accepted_batches(tmp_path):
    lines = [line(i, str(i)) for i in range(6)]
    api = LogApi([lines])
    loki = Loki(batch_size=2, fail_after=1)
    metrics = DecoMetrics(CollectorRegistry())
    config = LogsConfig(enabled=True, state_path=tmp_path / "watermark.json")
    await LogForwarder(api, loki, config, "mesh", metrics).poll("UTC")
    state = StateStore(config.state_path, "mesh:deco_router", metrics).load()
    assert loki.lines == lines[:2]
    assert state.anchor_hashes == [line_hash(x) for x in lines[:2]]


def test_invalid_or_different_state_resets(tmp_path):
    metrics = DecoMetrics(CollectorRegistry())
    path = tmp_path / "state.json"
    path.write_text("not json")
    assert StateStore(path, "mesh:deco_router", metrics).load().anchor_hashes == []
    StateStore(path, "mesh:deco_router", metrics).save(
        Watermark(1, "mesh:deco_router", None, ["x"], None)
    )
    assert StateStore(path, "other:deco_router", metrics).load().anchor_hashes == []


@pytest.mark.asyncio
async def test_probe_missing_ip_success_and_timeout_are_raw_observations():
    metrics = DecoMetrics(CollectorRegistry())
    prober = NodeProber(ProbesConfig(attempts=1, timeout=0.01), metrics)
    process = AsyncMock()
    process.wait.return_value = 0
    process.returncode = 0
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        await prober.probe([{"mac": "none"}, {"mac": "ok", "device_ip": "192.0.2.1"}])
    attempted = {
        (s.labels["device_mac"], s.value)
        for s in metrics.probe_attempted.collect()[0].samples
    }
    success = {
        (s.labels["device_mac"], s.value)
        for s in metrics.probe_success.collect()[0].samples
    }
    assert attempted == {("none", 0), ("ok", 1)}
    assert success == {("ok", 1)}
