import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from tplink_deco_exporter.config import LogsConfig, LokiConfig, ProbesConfig
from tplink_deco_exporter.log_forwarder import (
    LogForwarder,
    LokiPushFailed,
    MeshLogForwarder,
    StateStore,
    Watermark,
    line_hash,
)
from tplink_deco_exporter.probes import WebPortProber, web_port_from_host
from tplink_deco_exporter.prometheus_metrics import DecoMetrics


def line(second, body):
    return f"Thu Aug 27 12:00:{second:02d} 2026 daemon.info test: {body}"


class LogApi:
    def __init__(self, pages):
        self.pages = pages
        self.build_levels = []

    async def async_request(self, path, form, **kwargs):
        assert kwargs["expected_result_type"] is list
        return [{"name": "INFO", "value": 6}]

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
        self.lines_by_device = {}
        self.calls = 0
        self.fail_after = fail_after

    async def push(self, records, device_mac):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            return False
        lines = [line for _, line in records]
        self.lines.extend(lines)
        self.lines_by_device.setdefault(device_mac, []).extend(lines)
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
    forwarder = LogForwarder(api, loki, config, "mesh", metrics, "AA")
    await forwarder.poll("Asia/Jerusalem")
    assert loki.lines == old
    state = json.loads(config.state_path.read_text())
    assert state["nodes"]["AA"]["anchor_hashes"] == [line_hash(x) for x in old]
    assert set(state) == {
        "version",
        "identity",
        "nodes",
    }
    assert state["version"] == 2
    newer = line(4, "new unchanged  ")
    api.pages = [[old[2], old[3], newer], old[:2]]
    loki.lines.clear()
    await forwarder.poll("Asia/Jerusalem")
    assert loki.lines == [newer]


@pytest.mark.asyncio
async def test_blank_and_unparseable_records_are_dropped_and_observable(
    tmp_path, caplog
):
    valid = line(1, "valid")
    without_timestamp = "nonblank firmware content without a timestamp"
    invalid_timestamp = "2026-99-99 99:99:99 invalid firmware timestamp"
    api = LogApi([["", valid, "   ", without_timestamp, invalid_timestamp]])
    loki = Loki()
    registry = CollectorRegistry()
    metrics = DecoMetrics(registry)
    config = LogsConfig(enabled=True, state_path=tmp_path / "watermark.json")

    with caplog.at_level("ERROR", logger="tplink_deco_exporter.log_forwarder"):
        await LogForwarder(api, loki, config, "mesh", metrics, "AA").poll("UTC")

    assert loki.lines == [valid]
    output = generate_latest(registry).decode()
    assert 'deco_log_blank_records_total{device_mac="AA"} 2.0' in output
    assert (
        'deco_log_timestamp_parse_errors_total{device_mac="AA",'
        'reason="missing_prefix"} 1.0'
    ) in output
    assert (
        'deco_log_timestamp_parse_errors_total{device_mac="AA",'
        'reason="invalid_timestamp"} 1.0'
    ) in output
    assert caplog.text.count("Dropping firmware log record") == 2
    assert without_timestamp in caplog.text
    assert invalid_timestamp in caplog.text
    assert "leading timestamp did not match" in caplog.text
    assert "does not match format" in caplog.text


@pytest.mark.asyncio
async def test_partial_loki_delivery_updates_only_accepted_batches(tmp_path):
    lines = [line(i, str(i)) for i in range(6)]
    api = LogApi([lines])
    loki = Loki(batch_size=2, fail_after=1)
    metrics = DecoMetrics(CollectorRegistry())
    config = LogsConfig(enabled=True, state_path=tmp_path / "watermark.json")
    with pytest.raises(LokiPushFailed):
        await LogForwarder(api, loki, config, "mesh", metrics, "AA").poll("UTC")
    state = StateStore(config.state_path, "mesh:deco_router", metrics).load("AA")
    assert loki.lines == lines[:2]
    assert state.anchor_hashes == [line_hash(x) for x in lines[:2]]


def test_invalid_or_different_state_resets(tmp_path):
    metrics = DecoMetrics(CollectorRegistry())
    path = tmp_path / "state.json"
    path.write_text("not json")
    assert StateStore(path, "mesh:deco_router", metrics).load("AA").anchor_hashes == []
    StateStore(path, "mesh:deco_router", metrics).save(
        "AA", Watermark(None, ["x"], None)
    )
    assert StateStore(path, "other:deco_router", metrics).load("AA").anchor_hashes == []


def test_legacy_watermark_migrates_only_to_master_node(tmp_path):
    metrics = DecoMetrics(CollectorRegistry())
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "identity": "mesh:deco_router",
                "final_router_timestamp": "Thu Aug 27 12:00:01 2026",
                "anchor_hashes": ["legacy"],
                "last_loki_push_time": 1.0,
            }
        )
    )
    store = StateStore(path, "mesh:deco_router", metrics)

    assert store.load("satellite").anchor_hashes == []
    assert store.load("master", migrate_legacy=True).anchor_hashes == ["legacy"]
    persisted = json.loads(path.read_text())
    assert persisted["version"] == 2
    assert set(persisted["nodes"]) == {"master"}


@pytest.mark.asyncio
async def test_nodes_have_independent_replay_state_and_loki_labels(tmp_path):
    metrics = DecoMetrics(CollectorRegistry())
    config = LogsConfig(enabled=True, state_path=tmp_path / "state.json")
    store = StateStore(config.state_path, "mesh:deco_router", metrics)
    loki = Loki()
    first = line(1, "node-a")
    second = line(2, "node-b")
    node_a = LogForwarder(LogApi([[first]]), loki, config, "mesh", metrics, "AA", store)
    node_b = LogForwarder(
        LogApi([[second]]), loki, config, "mesh", metrics, "BB", store
    )

    await node_a.poll("UTC")
    await node_b.poll("UTC")

    assert loki.lines_by_device == {"AA": [first], "BB": [second]}
    persisted = json.loads(config.state_path.read_text())
    assert set(persisted["nodes"]) == {"AA", "BB"}
    assert persisted["nodes"]["AA"]["anchor_hashes"] == [line_hash(first)]
    assert persisted["nodes"]["BB"]["anchor_hashes"] == [line_hash(second)]


@pytest.mark.asyncio
async def test_mesh_log_poll_isolates_a_failing_node(tmp_path):
    class FailingLogApi(LogApi):
        async def async_request(self, path, form, **kwargs):
            raise RuntimeError("node unavailable")

    metrics = DecoMetrics(CollectorRegistry())
    config = LogsConfig(enabled=True, state_path=tmp_path / "state.json")
    loki = Loki()
    root_api = LogApi([[line(1, "master")]])
    mesh = MeshLogForwarder(
        root_api,
        lambda _ip: FailingLogApi([]),
        "192.0.2.1",
        loki,
        config,
        "mesh",
        metrics,
    )

    attempted, errors = await mesh.poll(
        [
            {"mac": "AA", "device_ip": "192.0.2.1"},
            {"mac": "BB", "device_ip": "192.0.2.2"},
        ],
        "UTC",
    )

    assert attempted == 2
    assert [(mac, type(error).__name__) for mac, error in errors] == [
        ("BB", "RuntimeError")
    ]
    assert loki.lines_by_device == {"AA": [line(1, "master")]}


@pytest.mark.asyncio
async def test_web_probe_missing_ip_success_and_timeout_are_raw_observations():
    metrics = DecoMetrics(CollectorRegistry())
    prober = WebPortProber(ProbesConfig(attempts=1, timeout=0.01), metrics, port=8443)
    writer = MagicMock()
    writer.wait_closed = AsyncMock()
    connections = []

    async def connect(address, port):
        connections.append((address, port))
        if address == "192.0.2.2":
            raise asyncio.TimeoutError
        return object(), writer

    with patch("asyncio.open_connection", connect):
        await prober.probe(
            [
                {"mac": "none"},
                {"mac": "ok", "device_ip": "192.0.2.1"},
                {"mac": "timeout", "device_ip": "192.0.2.2"},
            ]
        )
    attempted = {
        (s.labels["device_mac"], s.value)
        for s in metrics.web_probe_attempted.collect()[0].samples
    }
    success = {
        (s.labels["device_mac"], s.value)
        for s in metrics.web_probe_success.collect()[0].samples
    }
    durations = {
        s.labels["device_mac"] for s in metrics.web_probe_duration.collect()[0].samples
    }
    assert attempted == {("none", 0), ("ok", 1), ("timeout", 1)}
    assert success == {("ok", 1), ("timeout", 0)}
    assert durations == {"ok", "timeout"}
    assert connections == [("192.0.2.1", 8443), ("192.0.2.2", 8443)]
    writer.close.assert_called_once_with()


def test_web_port_is_derived_from_api_host():
    assert web_port_from_host("http://192.0.2.1") == 80
    assert web_port_from_host("https://192.0.2.1") == 443
    assert web_port_from_host("https://192.0.2.1:8443") == 8443
    assert web_port_from_host("192.0.2.1") == 80
