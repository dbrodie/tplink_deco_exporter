import pytest
from prometheus_client import CollectorRegistry, generate_latest

from tplink_deco_exporter.prometheus_metrics import DecoMetrics, DecoMetricsCollector

DEVICE = {
    "mac": "AA",
    "device_id": "node-a",
    "device_ip": "192.0.2.1",
    "device_model": "X60",
    "device_type": "deco",
    "nickname": "living_room",
    "custom_nickname": "Living Room",
    "role": "master",
    "parent_device_id": "",
    "hardware_ver": "1",
    "software_ver": "2",
    "hw_id": "hw",
    "oem_id": "oem",
    "product_level": "3",
    "group_status": "connected",
    "inet_status": "online",
    "inet_error_msg": "0",
    "previous": 7,
    "owner_transfer": "0",
    "port_count": "2",
    "nand_flash": 1,
    "oversized_firmware": False,
    "set_gateway_support": True,
    "speed_get_support": "1",
    "support_plc": 0,
    "bssid_2g": "b2",
    "bssid_5g": "b5",
    "bssid_sta_2g": "s2",
    "bssid_sta_5g": "s5",
    "connection_type": ["wired", "wireless"],
    "signal_level": {"band2_4": "0", "band5": "2", "band6": None},
    "topology": {"auto": 1, "device_id": "top-a"},
}
CLIENT = {
    "mac": "CC",
    "name": "Phone",
    "ip": "192.0.2.9",
    "online": "1",
    "access_host": "not-node-a",
    "client_mesh": 0,
    "client_type": "phone",
    "connection_type": "wireless",
    "wire_type": "wireless",
    "interface": "wlan0",
    "down_speed": "12.5",
    "up_speed": 3,
    "enable_priority": True,
    "remain_time": "60",
    "owner_id": "owner",
    "space_id": "space",
}


class FakeApi:
    def __init__(self):
        self.devices = [DEVICE]

    async def async_list_devices(self):
        return [dict(x) for x in self.devices]

    async def async_list_clients(self, mac):
        return [dict(CLIENT)] if mac == "AA" else []

    async def async_request(self, path, form, *args, **kwargs):
        return {
            "performance": {"cpu_usage": "0.25", "mem_usage": 0.5},
            "mode": {"sysmode": "AP", "workmode": "FAP"},
            "internet": {
                "link_status": "up",
                "ipv4": {
                    "inet_status": "online",
                    "dial_status": "connected",
                    "connect_type": "dynamic",
                    "auto_detect_type": "dhcp",
                    "error_code": "0",
                },
                "ipv6": {
                    "inet_status": "offline",
                    "dial_status": "idle",
                    "connect_type": "none",
                    "auto_detect_type": "none",
                    "error_code": "1",
                },
            },
            "wan_ipv4": {
                "wan": {
                    "dial_type": "dhcp",
                    "enable_auto_dns": 1,
                    "ip_info": {
                        "ip": "198.51.100.2",
                        "mask": "255.255.255.0",
                        "mac": "WA",
                        "gateway": "198.51.100.1",
                        "dns1": "1.1.1.1",
                        "dns2": "8.8.8.8",
                    },
                    "user_info": {"username": "must-not-leak"},
                },
                "lan": {
                    "ip_info": {"ip": "192.0.2.1", "mask": "255.255.255.0", "mac": "LA"}
                },
            },
            "lan_ipv4": {
                "lan": {"ip": "192.0.2.1", "mask": "255.255.255.0", "mac": "LA"}
            },
            "timesetting": {
                "date": "2026-08-27",
                "time": "12:00:00",
                "timezone": "UTC+2",
                "tz_region": "Asia/Jerusalem",
                "continent": "Asia",
                "dst_status": 1,
            },
            "operation_mode": {"mode": "11ax"},
            "power": {"support_dfs": 1},
            "ieee80211r": {"enable": 1},
            "beamforming": {"enable": 0},
            "extra_component_info": {"enable_erp": True},
        }[form]


@pytest.mark.asyncio
async def test_every_observed_inventory_group_is_exported_without_health_logic():
    registry = CollectorRegistry()
    metrics = DecoMetrics(registry)
    collector = DecoMetricsCollector(FakeApi(), metrics)
    collector.capabilities_checked = True
    await collector.collect_metrics()
    text = generate_latest(registry).decode()
    for name in (
        "deco_device_info",
        "deco_device_signal_level",
        "deco_client_info",
        "deco_gateway_cpu_usage_ratio",
        "deco_system_mode_info",
        "deco_internet_stack_info",
        "deco_wan_info",
        "deco_lan_info",
        "deco_router_time_seconds",
        "deco_wireless_fast_roaming_enabled",
    ):
        assert name in text
    assert 'band="5",device_mac="AA"} 2.0' in text
    assert 'client_mac="CC",device_mac="AA"} 12.5' in text
    assert "must-not-leak" not in text
    assert "health" not in text and "degraded" not in text


@pytest.mark.asyncio
async def test_stale_device_and_client_series_are_removed():
    api = FakeApi()
    registry = CollectorRegistry()
    metrics = DecoMetrics(registry)
    collector = DecoMetricsCollector(api, metrics)
    collector.capabilities_checked = True
    await collector.collect_metrics()
    assert 'device_mac="AA"' in generate_latest(registry).decode()
    api.devices = []
    await collector.collect_metrics()
    output = generate_latest(registry).decode()
    assert 'device_mac="AA"' not in output
