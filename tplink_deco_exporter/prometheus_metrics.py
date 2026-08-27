from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter as ValueCounter
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from prometheus_client import REGISTRY, Counter, Gauge, Histogram

from .api import TplinkDecoApi

_LOGGER = logging.getLogger(__name__)

_DEVICE_FIELDS = {
    "mac",
    "device_id",
    "device_ip",
    "device_model",
    "device_type",
    "nickname",
    "custom_nickname",
    "role",
    "parent_device_id",
    "hardware_ver",
    "software_ver",
    "hw_id",
    "oem_id",
    "product_level",
    "group_status",
    "inet_status",
    "inet_error_msg",
    "previous",
    "owner_transfer",
    "port_count",
    "nand_flash",
    "oversized_firmware",
    "set_gateway_support",
    "speed_get_support",
    "support_plc",
    "bssid_2g",
    "bssid_5g",
    "bssid_sta_2g",
    "bssid_sta_5g",
    "connection_type",
    "signal_level",
    "topology",
}
_CLIENT_FIELDS = {
    "mac",
    "name",
    "ip",
    "online",
    "access_host",
    "client_mesh",
    "client_type",
    "connection_type",
    "wire_type",
    "interface",
    "down_speed",
    "up_speed",
    "enable_priority",
    "remain_time",
    "owner_id",
    "space_id",
}


def _debug_unmapped(context: str, value: dict[str, Any], known: set[str]):
    unmapped = {
        name: type(field).__name__ for name, field in value.items() if name not in known
    }
    if unmapped:
        _LOGGER.debug("%s unmapped fields=%s", context, unmapped)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _n(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _b(value: Any) -> float:
    if isinstance(value, str):
        return float(value.lower() in {"1", "true", "yes", "on", "enabled"})
    return float(bool(value))


class DecoMetrics:
    def __init__(self, registry=REGISTRY):
        kw = {"registry": registry}
        self.device_info = Gauge(
            "deco_device_info",
            "Deco device inventory",
            [
                "device_mac",
                "device_id",
                "ip",
                "model",
                "device_type",
                "nickname",
                "custom_nickname",
                "role",
                "parent_device_id",
                "hardware_ver",
                "software_ver",
                "hw_id",
                "oem_id",
                "product_level",
            ],
            **kw,
        )
        self.device_group = Gauge(
            "deco_device_group_status_info",
            "Raw group status",
            ["device_mac", "status"],
            **kw,
        )
        self.device_inet = Gauge(
            "deco_device_inet_status_info",
            "Raw internet status",
            ["device_mac", "status"],
            **kw,
        )
        self.device_error = Gauge(
            "deco_device_inet_error_info",
            "Raw internet error",
            ["device_mac", "error"],
            **kw,
        )
        self.connection = Gauge(
            "deco_device_connection_type_info",
            "Raw connection types",
            ["device_mac", "type"],
            **kw,
        )
        self.signal = Gauge(
            "deco_device_signal_level",
            "Raw firmware signal level",
            ["device_mac", "band"],
            **kw,
        )
        self.bssid = Gauge(
            "deco_device_bssid_info",
            "BSSID inventory",
            ["device_mac", "band", "kind", "bssid"],
            **kw,
        )
        self.topology = Gauge(
            "deco_device_topology_info",
            "Raw topology",
            ["device_mac", "parent_device_id", "topology_device_id", "auto"],
            **kw,
        )
        self.product_level = Gauge(
            "deco_device_product_level", "Raw product level", ["device_mac"], **kw
        )
        self.port_count = Gauge(
            "deco_device_port_count", "Port count", ["device_mac"], **kw
        )
        self.previous = Gauge(
            "deco_device_previous_info",
            "Raw previous scalar",
            ["device_mac", "value"],
            **kw,
        )
        self.device_boolean = {
            name: Gauge(f"deco_device_{name}", f"Raw {name} flag", ["device_mac"], **kw)
            for name in (
                "owner_transfer",
                "nand_flash",
                "oversized_firmware",
                "set_gateway_support",
                "speed_get_support",
                "support_plc",
            )
        }

        client_labels = [
            "client_mac",
            "name",
            "ip",
            "device_mac",
            "access_host",
            "client_type",
            "connection_type",
            "wire_type",
            "interface",
            "owner_id",
            "space_id",
        ]
        self.client_info = Gauge(
            "deco_client_info", "Per-node client inventory", client_labels, **kw
        )
        self.client_online = Gauge(
            "deco_client_online",
            "Raw client online flag",
            ["client_mac", "device_mac"],
            **kw,
        )
        self.client_mesh = Gauge(
            "deco_client_mesh",
            "Raw client mesh flag",
            ["client_mac", "device_mac"],
            **kw,
        )
        self.client_priority = Gauge(
            "deco_client_priority_enabled",
            "Raw client priority flag",
            ["client_mac", "device_mac"],
            **kw,
        )
        self.client_remaining = Gauge(
            "deco_client_remaining_time_seconds",
            "Raw remaining time",
            ["client_mac", "device_mac"],
            **kw,
        )
        self.client_down = Gauge(
            "deco_client_download_kilobytes_per_second",
            "Raw download rate",
            ["client_mac", "device_mac"],
            **kw,
        )
        self.client_up = Gauge(
            "deco_client_upload_kilobytes_per_second",
            "Raw upload rate",
            ["client_mac", "device_mac"],
            **kw,
        )
        self.device_client_count = Gauge(
            "deco_device_client_count",
            "Clients returned per Deco",
            ["device_mac"],
            **kw,
        )
        self.client_count_connection = Gauge(
            "deco_device_client_count_by_connection_type",
            "Clients by raw connection type",
            ["device_mac", "connection_type"],
            **kw,
        )
        self.client_count_interface = Gauge(
            "deco_device_client_count_by_interface",
            "Clients by interface",
            ["device_mac", "interface"],
            **kw,
        )
        self.client_count_wire = Gauge(
            "deco_device_client_count_by_wire_type",
            "Clients by wire type",
            ["device_mac", "wire_type"],
            **kw,
        )

        self.cpu = Gauge(
            "deco_gateway_cpu_usage_ratio", "Raw normalized CPU usage", **kw
        )
        self.memory = Gauge(
            "deco_gateway_memory_usage_ratio", "Raw normalized memory usage", **kw
        )
        self.system_mode = Gauge(
            "deco_system_mode_info", "System mode", ["sysmode", "workmode"], **kw
        )
        self.wan_link = Gauge(
            "deco_wan_link_status_info", "WAN link status", ["status"], **kw
        )
        self.internet_stack = Gauge(
            "deco_internet_stack_info",
            "Internet stack state",
            [
                "protocol",
                "inet_status",
                "dial_status",
                "connect_type",
                "auto_detect_type",
                "error_code",
            ],
            **kw,
        )
        self.wan_info = Gauge(
            "deco_wan_info",
            "Non-secret WAN addressing",
            ["dial_type", "ip", "mask", "mac", "gateway", "dns1", "dns2"],
            **kw,
        )
        self.wan_auto_dns = Gauge(
            "deco_wan_auto_dns_enabled", "Raw WAN auto-DNS flag", **kw
        )
        self.lan_info = Gauge(
            "deco_lan_info", "LAN addressing", ["source", "ip", "mask", "mac"], **kw
        )
        self.time_info = Gauge(
            "deco_time_settings_info",
            "Router timezone settings",
            ["timezone", "tz_region", "continent", "dst_status"],
            **kw,
        )
        self.router_time = Gauge(
            "deco_router_time_seconds", "Router wall clock as Unix seconds", **kw
        )
        self.wireless_mode = Gauge(
            "deco_wireless_operation_mode_info",
            "Wireless operation mode",
            ["mode"],
            **kw,
        )
        self.dfs = Gauge("deco_wireless_dfs_supported", "Raw DFS support flag", **kw)
        self.fast_roaming = Gauge(
            "deco_wireless_fast_roaming_enabled", "Raw 802.11r flag", **kw
        )
        self.beamforming = Gauge(
            "deco_wireless_beamforming_enabled", "Raw beamforming flag", **kw
        )
        self.erp = Gauge("deco_wireless_erp_enabled", "Raw ERP flag", **kw)

        self.endpoint_supported = Gauge(
            "deco_api_endpoint_supported",
            "Whether an API endpoint is supported",
            ["endpoint"],
            **kw,
        )
        self.api_requests = Counter(
            "deco_api_requests_total", "API requests", ["endpoint", "result"], **kw
        )
        self.api_duration = Histogram(
            "deco_api_request_duration_seconds",
            "API request duration",
            ["endpoint"],
            **kw,
        )
        self.api_last_success = Gauge(
            "deco_api_last_success_timestamp_seconds",
            "Last successful API request",
            ["endpoint"],
            **kw,
        )
        self.collection_duration = Gauge(
            "deco_collection_duration_seconds",
            "Collection duration",
            ["collector"],
            **kw,
        )
        self.collection_last_success = Gauge(
            "deco_collection_last_success_timestamp_seconds",
            "Last successful collection",
            ["collector"],
            **kw,
        )
        self.collection_errors = Counter(
            "deco_collection_errors_total",
            "Collection errors",
            ["collector", "error_type"],
            **kw,
        )
        self.devices_returned = Gauge("deco_devices_returned", "Devices returned", **kw)
        self.clients_returned = Gauge("deco_clients_returned", "Clients returned", **kw)
        self.probe_attempted = Gauge(
            "deco_device_probe_attempted",
            "Whether a node probe was attempted",
            ["device_mac"],
            **kw,
        )
        self.probe_success = Gauge(
            "deco_device_probe_success", "Raw node probe result", ["device_mac"], **kw
        )
        self.probe_duration = Gauge(
            "deco_device_probe_duration_seconds",
            "Node probe duration",
            ["device_mac"],
            **kw,
        )
        self.log_pages = Gauge(
            "deco_log_snapshot_pages", "Pages in latest log snapshot", **kw
        )
        self.log_records = Counter(
            "deco_log_records_read_total", "Log records read", **kw
        )
        self.loki_batches = Counter(
            "deco_loki_batches_total", "Loki batches", ["result"], **kw
        )
        self.loki_records = Counter(
            "deco_loki_records_total", "Loki records", ["result"], **kw
        )
        self.watermark_resets = Counter(
            "deco_log_watermark_resets_total", "Watermark resets", ["reason"], **kw
        )

        self.dynamic = [
            self.device_info,
            self.device_group,
            self.device_inet,
            self.device_error,
            self.connection,
            self.signal,
            self.bssid,
            self.topology,
            self.product_level,
            self.port_count,
            self.previous,
            *self.device_boolean.values(),
            self.client_info,
            self.client_online,
            self.client_mesh,
            self.client_priority,
            self.client_remaining,
            self.client_down,
            self.client_up,
            self.device_client_count,
            self.client_count_connection,
            self.client_count_interface,
            self.client_count_wire,
        ]

    def clear_inventory(self):
        for metric in self.dynamic:
            metric.clear()


REQUIRED_ENDPOINTS = {
    "device_list": ("device", "device_list"),
    "mode": ("device", "mode"),
    "timesetting": ("device", "timesetting"),
    "performance": ("network", "performance"),
    "internet": ("network", "internet"),
    "wan_ipv4": ("network", "wan_ipv4"),
    "lan_ipv4": ("network", "lan_ipv4"),
    "operation_mode": ("wireless", "operation_mode"),
    "power": ("wireless", "power"),
    "ieee80211r": ("wireless", "ieee80211r"),
    "beamforming": ("wireless", "beamforming"),
    "extra_component_info": ("web", "extra_component_info"),
    "log_types": ("log_export", "types"),
    "feedback_log": ("log_export", "feedback_log"),
}
OPTIONAL_ENDPOINTS = {
    "signal_level_list": ("device", "signal_level_list"),
    "traffic_stat": ("client", "traffic_stat"),
    "dhcp_lease_list": ("client", "dhcp_lease_list"),
    "speedtest": ("network", "speedtest"),
    "speedtest_server": ("network", "speedtest_server"),
    "gateway_system": ("device", "system"),
    "eco_mode": ("device", "eco_mode"),
    "fixed_wan_port": ("network", "fixed_wan_port"),
    "detect_mode": ("network", "detect_mode"),
    "network_optimization": ("network", "optimization"),
    "wireless_bridge": ("wireless", "bridge"),
    "ofdma": ("wireless", "ofdma"),
    "backhaul_optimization": ("wireless", "backhaul_optimization"),
    "security_info": ("security", "info"),
    "security_history": ("security", "history"),
    "dhcp_info": ("network", "dhcp"),
    "legacy_log": ("log", "log"),
    "firmware_config": ("firmware", "config"),
}


class DecoMetricsCollector:
    def __init__(self, api: TplinkDecoApi, metrics: DecoMetrics):
        self.api, self.m = api, metrics
        self.devices: list[dict[str, Any]] = []
        self.time_settings: dict[str, Any] = {}
        self.capabilities_checked = False

    async def request(
        self, endpoint: str, call: Callable[[], Awaitable[Any]], optional=False
    ):
        start = time.monotonic()
        try:
            result = await call()
        except Exception:
            self.m.api_requests.labels(endpoint, "error").inc()
            if optional:
                self.m.endpoint_supported.labels(endpoint).set(0)
            raise
        else:
            self.m.api_requests.labels(endpoint, "success").inc()
            self.m.api_last_success.labels(endpoint).set(time.time())
            self.m.endpoint_supported.labels(endpoint).set(1)
            return result
        finally:
            self.m.api_duration.labels(endpoint).observe(time.monotonic() - start)

    async def collect_metrics(self):
        started = time.monotonic()
        try:
            devices = await self.request("device_list", self.api.async_list_devices)
            self.devices = devices
            self.m.clear_inventory()
            self._devices(devices)
            client_lists = await asyncio.gather(
                *(self._clients(d) for d in devices), return_exceptions=True
            )
            self.m.clients_returned.set(
                sum(len(x) for x in client_lists if isinstance(x, list))
            )
            await self._global()
            if not self.capabilities_checked:
                await self._capabilities()
                self.capabilities_checked = True
        except Exception as exc:
            self.m.collection_errors.labels("metrics", type(exc).__name__).inc()
            raise
        else:
            self.m.collection_last_success.labels("metrics").set(time.time())
        finally:
            self.m.collection_duration.labels("metrics").set(time.monotonic() - started)

    def _devices(self, devices):
        self.m.devices_returned.set(len(devices))
        for d in devices:
            _debug_unmapped("device_list", d, _DEVICE_FIELDS)
            mac = _s(d.get("mac"))
            nickname = d.get("nickname")
            self.m.device_info.labels(
                mac,
                _s(d.get("device_id")),
                _s(d.get("device_ip")),
                _s(d.get("device_model")),
                _s(d.get("device_type")),
                _s(nickname),
                _s(d.get("custom_nickname")),
                _s(d.get("role")),
                _s(d.get("parent_device_id")),
                _s(d.get("hardware_ver")),
                _s(d.get("software_ver")),
                _s(d.get("hw_id")),
                _s(d.get("oem_id")),
                _s(d.get("product_level")),
            ).set(1)
            if d.get("group_status") is not None:
                self.m.device_group.labels(mac, _s(d["group_status"])).set(1)
            if d.get("inet_status") is not None:
                self.m.device_inet.labels(mac, _s(d["inet_status"])).set(1)
            if d.get("inet_error_msg") is not None:
                self.m.device_error.labels(mac, _s(d["inet_error_msg"])).set(1)
            for kind in d.get("connection_type") or []:
                self.m.connection.labels(mac, _s(kind)).set(1)
            for band, value in (d.get("signal_level") or {}).items():
                if value is not None:
                    self.m.signal.labels(
                        mac,
                        band.replace("band2_4", "2.4")
                        .replace("band5", "5")
                        .replace("band6", "6"),
                    ).set(_n(value))
            for field, band, kind in (
                ("bssid_2g", "2.4", "ap"),
                ("bssid_5g", "5", "ap"),
                ("bssid_sta_2g", "2.4", "station"),
                ("bssid_sta_5g", "5", "station"),
            ):
                if d.get(field) is not None:
                    self.m.bssid.labels(mac, band, kind, _s(d[field])).set(1)
            if d.get("topology") is not None:
                topology = d["topology"] or {}
                self.m.topology.labels(
                    mac,
                    _s(d.get("parent_device_id")),
                    _s(topology.get("device_id")),
                    _s(topology.get("auto")),
                ).set(1)
            if d.get("product_level") is not None:
                self.m.product_level.labels(mac).set(_n(d["product_level"]))
            if d.get("port_count") is not None:
                self.m.port_count.labels(mac).set(_n(d["port_count"]))
            if d.get("previous") is not None:
                self.m.previous.labels(mac, _s(d["previous"])).set(1)
            for name, metric in self.m.device_boolean.items():
                if d.get(name) is not None:
                    metric.labels(mac).set(_b(d[name]))

    async def _clients(self, device):
        mac = _s(device.get("mac"))
        try:
            clients = await self.request(
                "client_list", lambda: self.api.async_list_clients(mac)
            )
        except Exception as exc:  # noqa: BLE001 - isolate one node's client collector
            self.m.collection_errors.labels("clients", type(exc).__name__).inc()
            return exc
        self.m.device_client_count.labels(mac).set(len(clients))
        for field, metric in (
            ("connection_type", self.m.client_count_connection),
            ("interface", self.m.client_count_interface),
            ("wire_type", self.m.client_count_wire),
        ):
            for value, count in ValueCounter(_s(c.get(field)) for c in clients).items():
                metric.labels(mac, value).set(count)
        for c in clients:
            _debug_unmapped("client_list", c, _CLIENT_FIELDS)
            cmac = _s(c.get("mac"))
            base = (cmac, mac)
            self.m.client_info.labels(
                cmac,
                _s(c.get("name")),
                _s(c.get("ip")),
                mac,
                _s(c.get("access_host")),
                _s(c.get("client_type")),
                _s(c.get("connection_type")),
                _s(c.get("wire_type")),
                _s(c.get("interface")),
                _s(c.get("owner_id")),
                _s(c.get("space_id")),
            ).set(1)
            for field, metric, converter in (
                ("online", self.m.client_online, _b),
                ("client_mesh", self.m.client_mesh, _b),
                ("enable_priority", self.m.client_priority, _b),
                ("remain_time", self.m.client_remaining, _n),
                ("down_speed", self.m.client_down, _n),
                ("up_speed", self.m.client_up, _n),
            ):
                if c.get(field) is not None:
                    metric.labels(*base).set(converter(c[field]))
        return clients

    async def _get(self, endpoint):
        path, form = REQUIRED_ENDPOINTS[endpoint]
        return await self.request(endpoint, lambda: self.api.async_request(path, form))

    async def _global(self):
        results = await asyncio.gather(
            *(
                self._get(e)
                for e in (
                    "performance",
                    "mode",
                    "internet",
                    "wan_ipv4",
                    "lan_ipv4",
                    "timesetting",
                    "operation_mode",
                    "power",
                    "ieee80211r",
                    "beamforming",
                    "extra_component_info",
                )
            )
        )
        perf, mode, internet, wan, lan, ts, op, power, roaming, beam, extra = results
        for endpoint, value, known in (
            ("performance", perf, {"cpu_usage", "mem_usage"}),
            ("mode", mode, {"sysmode", "workmode"}),
            ("internet", internet, {"link_status", "ipv4", "ipv6"}),
            ("wan_ipv4", wan, {"wan", "lan"}),
            ("lan_ipv4", lan, {"lan"}),
            (
                "timesetting",
                ts,
                {"date", "time", "timezone", "tz_region", "continent", "dst_status"},
            ),
            ("operation_mode", op, {"mode"}),
            ("power", power, {"support_dfs"}),
            ("ieee80211r", roaming, {"enable"}),
            ("beamforming", beam, {"enable"}),
            ("extra_component_info", extra, {"enable_erp"}),
        ):
            _debug_unmapped(endpoint, value, known)
        self.m.cpu.set(_n(perf.get("cpu_usage")))
        self.m.memory.set(_n(perf.get("mem_usage")))
        self.m.system_mode.clear()
        self.m.system_mode.labels(
            _s(mode.get("sysmode")), _s(mode.get("workmode"))
        ).set(1)
        self.m.wan_link.clear()
        self.m.wan_link.labels(_s(internet.get("link_status"))).set(1)
        self.m.internet_stack.clear()
        for protocol in ("ipv4", "ipv6"):
            p = internet.get(protocol) or {}
            self.m.internet_stack.labels(
                protocol,
                _s(p.get("inet_status")),
                _s(p.get("dial_status")),
                _s(p.get("connect_type")),
                _s(p.get("auto_detect_type")),
                _s(p.get("error_code")),
            ).set(1)
        w = wan.get("wan") or {}
        wi = w.get("ip_info") or {}
        self.m.wan_info.clear()
        self.m.wan_info.labels(
            _s(w.get("dial_type")),
            _s(wi.get("ip")),
            _s(wi.get("mask")),
            _s(wi.get("mac")),
            _s(wi.get("gateway")),
            _s(wi.get("dns1")),
            _s(wi.get("dns2")),
        ).set(1)
        self.m.wan_auto_dns.set(_b(w.get("enable_auto_dns")))
        self.m.lan_info.clear()
        for source, block in (
            ("wan_ipv4", (wan.get("lan") or {}).get("ip_info") or {}),
            ("lan_ipv4", lan.get("lan") or {}),
        ):
            self.m.lan_info.labels(
                source, _s(block.get("ip")), _s(block.get("mask")), _s(block.get("mac"))
            ).set(1)
        self.time_settings = ts
        self.m.time_info.clear()
        self.m.time_info.labels(
            _s(ts.get("timezone")),
            _s(ts.get("tz_region")),
            _s(ts.get("continent")),
            _s(ts.get("dst_status")),
        ).set(1)
        try:
            raw = f"{ts['date']} {ts['time']}"
            pattern = "%Y/%m/%d %H:%M:%S" if "/" in raw else "%Y-%m-%d %H:%M:%S"
            self.m.router_time.set(
                datetime.strptime(raw, pattern)
                .replace(tzinfo=ZoneInfo(_s(ts.get("tz_region")) or "UTC"))
                .timestamp()
            )
        except (KeyError, ValueError, ZoneInfoNotFoundError):
            pass
        self.m.wireless_mode.clear()
        self.m.wireless_mode.labels(_s(op.get("mode"))).set(1)
        self.m.dfs.set(_b(power.get("support_dfs")))
        self.m.fast_roaming.set(_b(roaming.get("enable")))
        self.m.beamforming.set(_b(beam.get("enable")))
        self.m.erp.set(_b(extra.get("enable_erp")))

    async def _capabilities(self):
        for endpoint in REQUIRED_ENDPOINTS:
            self.m.endpoint_supported.labels(endpoint).set(1)
        for endpoint, (path, form) in OPTIONAL_ENDPOINTS.items():
            try:
                await self.request(
                    endpoint,
                    lambda p=path, f=form: self.api.async_request(p, f),
                    optional=True,
                )
            except Exception:  # noqa: BLE001,S110 - unsupported forms are capabilities
                pass
