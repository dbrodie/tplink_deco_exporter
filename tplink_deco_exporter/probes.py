from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from urllib.parse import urlsplit

from .config import ProbesConfig
from .prometheus_metrics import DecoMetrics


def web_port_from_host(host: str) -> int:
    parsed = urlsplit(host if "://" in host else f"http://{host}")
    if parsed.port is not None:
        return parsed.port
    return 443 if parsed.scheme.lower() == "https" else 80


class WebPortProber:
    def __init__(self, config: ProbesConfig, metrics: DecoMetrics, port: int):
        self.config, self.metrics, self.port = config, metrics, port

    async def probe(self, devices: list[dict]):
        self.metrics.web_probe_attempted.clear()
        self.metrics.web_probe_success.clear()
        self.metrics.web_probe_duration.clear()
        await asyncio.gather(*(self._one(device) for device in devices))

    async def _one(self, device: dict):
        mac, address = str(device.get("mac", "")), device.get("device_ip")
        if not address:
            self.metrics.web_probe_attempted.labels(mac).set(0)
            return
        self.metrics.web_probe_attempted.labels(mac).set(1)
        started = time.monotonic()
        success = False
        for _ in range(self.config.attempts):
            try:
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(str(address), self.port),
                    timeout=self.config.timeout,
                )
                success = True
                writer.close()
                with suppress(OSError, asyncio.TimeoutError):
                    await asyncio.wait_for(
                        writer.wait_closed(), timeout=self.config.timeout
                    )
            except (OSError, asyncio.TimeoutError):
                success = False
            if success:
                break
        self.metrics.web_probe_duration.labels(mac).set(time.monotonic() - started)
        self.metrics.web_probe_success.labels(mac).set(float(success))
