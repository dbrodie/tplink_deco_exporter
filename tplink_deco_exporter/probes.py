from __future__ import annotations

import asyncio
import time

from .config import ProbesConfig
from .prometheus_metrics import DecoMetrics


class NodeProber:
    def __init__(self, config: ProbesConfig, metrics: DecoMetrics):
        self.config, self.metrics = config, metrics

    async def probe(self, devices: list[dict]):
        self.metrics.probe_attempted.clear()
        self.metrics.probe_success.clear()
        self.metrics.probe_duration.clear()
        await asyncio.gather(*(self._one(device) for device in devices))

    async def _one(self, device: dict):
        mac, address = str(device.get("mac", "")), device.get("device_ip")
        if not address:
            self.metrics.probe_attempted.labels(mac).set(0)
            return
        self.metrics.probe_attempted.labels(mac).set(1)
        started = time.monotonic()
        success = False
        for _ in range(self.config.attempts):
            try:
                process = await asyncio.create_subprocess_exec(
                    "ping",
                    "-c",
                    "1",
                    "-W",
                    str(max(1, int(self.config.timeout))),
                    str(address),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(process.wait(), timeout=self.config.timeout + 1)
                success = process.returncode == 0
            except (OSError, asyncio.TimeoutError):
                success = False
            if success:
                break
        self.metrics.probe_duration.labels(mac).set(time.monotonic() - started)
        self.metrics.probe_success.labels(mac).set(float(success))
